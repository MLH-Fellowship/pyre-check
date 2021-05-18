# Copyright (c) Facebook, Inc. and its affiliates.
#
# This source code is licensed under the MIT license found in the
# LICENSE file in the root directory of this source tree.


"""
This is a temporary implementation of Pysa's language server. This is an exact copy
of Pyre's language server, which lives in persistent.py.
"""

import asyncio
import logging
import os
import traceback
from pathlib import Path
from typing import Optional, AsyncIterator, Sequence, Dict

from ... import (
    json_rpc,
    error,
    command_arguments,
    commands,
    configuration as configuration_module,
)
from . import (
    language_server_protocol as lsp,
    server_connection,
    async_server_connection as connection,
    start,
    stop,
    incremental,
)
from .persistent import (
    LSPEvent,
    ServerState,
    CONSECUTIVE_START_ATTEMPT_THRESHOLD,
    _read_lsp_request,
    try_initialize,
    _start_pyre_server,
    _log_lsp_event,
    parse_subscription_response,
    StartSuccess,
    StartFailure,
    _publish_diagnostics,
    invalid_models_to_diagnostics,
    InitializationExit,
    InitializationSuccess,
    InitializationFailure,
)
from api import query, connection as api_connection

LOG: logging.Logger = logging.getLogger(__name__)

PYSA_HANDLER_OBJ = None


async def update_errors() -> None:
    pyre_connection = api_connection.PyreConnection(
        Path(PYSA_HANDLER_OBJ.pyre_arguments.global_root)
    )
    initial_model_errors = query.get_invalid_taint_models(pyre_connection)
    PYSA_HANDLER_OBJ.update_model_errors(initial_model_errors)
    await PYSA_HANDLER_OBJ.show_model_errors_to_client()


class PysaServerHandler(connection.BackgroundTask):
    binary_location: str
    server_identifier: str
    pyre_arguments: start.Arguments
    client_output_channel: connection.TextWriter
    server_state: ServerState

    def __init__(
        self,
        binary_location: str,
        server_identifier: str,
        pyre_arguments: start.Arguments,
        client_output_channel: connection.TextWriter,
        server_state: ServerState,
    ) -> None:
        self.binary_location = binary_location
        self.server_identifier = server_identifier
        self.pyre_arguments = pyre_arguments
        self.client_output_channel = client_output_channel
        self.server_state = server_state

    async def show_message_to_client(
        self, message: str, level: lsp.MessageType = lsp.MessageType.INFO
    ) -> None:
        await lsp.write_json_rpc(
            self.client_output_channel,
            json_rpc.Request(
                method="window/showMessage",
                parameters=json_rpc.ByNameParameters(
                    {"type": int(level), "message": message}
                ),
            ),
        )

    async def log_and_show_message_to_client(
        self, message: str, level: lsp.MessageType = lsp.MessageType.INFO
    ) -> None:
        if level == lsp.MessageType.ERROR:
            LOG.error(message)
        elif level == lsp.MessageType.WARNING:
            LOG.warning(message)
        elif level == lsp.MessageType.INFO:
            LOG.info(message)
        else:
            LOG.debug(message)
        await self.show_message_to_client(message, level)

    # ** NEW **
    # Updating model errors from Pysa
    def update_model_errors(self, model_errors: Sequence[query.InvalidModel]) -> None:
        # LOG.info(
        #     (
        #         "Refreshing errors received from Pysa server. "
        #         + f"Total number of errors is {len(model_errors)}."
        #     )
        # )
        self.server_state.diagnostics = invalid_models_to_diagnostics(model_errors)
        # LOG.info(f"update_model_errors(): server_state.diagnostics = {self.server_state.diagnostics}")

    # ** NEW **
    # Show invalid model errors from Pysa to the client
    async def show_model_errors_to_client(self) -> None:
        # LOG.info(f"server_state.opened_documents = {self.server_state.opened_documents}")
        for path in self.server_state.opened_documents:
            path_str = str(path.resolve())
            # path = "/home/momo/Documents/Programs/pyre-check/documentation/pysa_tutorial/exercise2/sources_sinks.pysa"
            await _publish_diagnostics(self.client_output_channel, path_str, [])
            diagnostics = self.server_state.diagnostics.get(path_str, None)
            # LOG.info(f"show_model_errors_to_client(): diagnostics = {diagnostics}")
            if diagnostics is not None:
                await _publish_diagnostics(
                    self.client_output_channel, path_str, diagnostics
                )

    @connection.asynccontextmanager
    async def _read_server_response(
        self, server_input_channel: connection.TextReader
    ) -> AsyncIterator[str]:
        try:
            raw_response = await server_input_channel.read_until(separator="\n")
            yield raw_response
        except incremental.InvalidServerResponse as error:
            LOG.error(f"Pysa server returns invalid response: {error}")

    # ** NEW **
    # Subscribe and read the server response
    async def _subscribe_to_model_error(
        self,
        server_input_channel: connection.TextReader,
        server_output_channel: connection.TextWriter,
    ) -> None:
        # pyre_connection = api_connection.PyreConnection(Path(self.pyre_arguments.global_root))
        subscription_name = f"persistent_{os.getpid()}"
        await server_output_channel.write(
            f'["SubscribeToModelErrors", "{subscription_name}"]\n'
        )
        while True:
            async with self._read_server_response(server_input_channel):
                await update_errors()
                # initial_model_errors = query.get_invalid_taint_models(pyre_connection)
                # self.update_model_errors(initial_model_errors)
                # await self.show_model_errors_to_client()
        # async with self._read_server_response(server_input_channel):
        #     initial_model_errors = query.get_invalid_taint_models(pyre_connection)
        #     self.update_model_errors(initial_model_errors)
        #     await self.show_model_errors_to_client()

        # while True:
        #     async with self._read_server_response(
        #         server_input_channel
        #     ) as raw_subscription_response:
        #         subscription_response = parse_subscription_response(
        #             raw_subscription_response
        #         )
        #         if subscription_name == subscription_response.name:
        #             self.update_model_errors(subscription_response.body)
        #             await self.show_model_errors_to_client()

    # ** NEW **
    # Call the above method to read the server response
    async def subscribe_to_model_error(
        self,
        server_input_channel: connection.TextReader,
        server_output_channel: connection.TextWriter,
    ) -> None:
        try:
            await self._subscribe_to_model_error(
                server_input_channel, server_output_channel
            )
        finally:
            await self.show_message_to_client(
                "Lost connection to background Pysa server.",
                level=lsp.MessageType.WARNING,
            )
            self.server_state.diagnostics = {}
            await self.show_model_errors_to_client()

    def _auxiliary_logging_info(self) -> Dict[str, Optional[str]]:
        return {
            "binary": self.binary_location,
            "log_path": self.pyre_arguments.log_path,
            "global_root": self.pyre_arguments.global_root,
            **(
                {}
                if self.pyre_arguments.local_root is None
                else {"local_root": self.pyre_arguments.relative_local_root}
            ),
        }

    async def _run(self) -> None:
        socket_path = server_connection.get_default_socket_path(
            log_directory=Path(self.pyre_arguments.log_path)
        )
        try:
            async with connection.connect_in_text_mode(socket_path) as (
                input_channel,
                output_channel,
            ):
                await self.log_and_show_message_to_client(
                    (
                        "Established connection with existing Pyre server at "
                        + f"`{self.server_identifier}`."
                    )
                )
                self.server_state.consecutive_start_failure = 0
                _log_lsp_event(
                    remote_logging=self.pyre_arguments.remote_logging,
                    event=LSPEvent.CONNECTED,
                    normals={
                        "connected_to": "already_running_server",
                        **self._auxiliary_logging_info(),
                    },
                )
                await self.subscribe_to_model_error(input_channel, output_channel)
        except connection.ConnectionFailure:
            await self.log_and_show_message_to_client(
                (
                    f"Starting a new Pysa server at `{self.server_identifier}` in "
                    + "the background..."
                )
            )

            start_status = await _start_pyre_server(
                self.binary_location, self.pyre_arguments
            )
            if isinstance(start_status, StartSuccess):
                # await self.log_and_show_message_to_client(
                #     f"Pysa server at `{self.server_identifier}` has been initialized."
                # )

                async with connection.connect_in_text_mode(socket_path) as (
                    input_channel,
                    output_channel,
                ):
                    self.server_state.consecutive_start_failure = 0
                    _log_lsp_event(
                        remote_logging=self.pyre_arguments.remote_logging,
                        event=LSPEvent.CONNECTED,
                        normals={
                            "connected_to": "newly_started_server",
                            **self._auxiliary_logging_info(),
                        },
                    )
                    await self.subscribe_to_model_error(input_channel, output_channel)
            elif isinstance(start_status, StartFailure):
                self.server_state.consecutive_start_failure += 1
                if (
                    self.server_state.consecutive_start_failure
                    < CONSECUTIVE_START_ATTEMPT_THRESHOLD
                ):
                    _log_lsp_event(
                        remote_logging=self.pyre_arguments.remote_logging,
                        event=LSPEvent.NOT_CONNECTED,
                        normals={
                            **self._auxiliary_logging_info(),
                            "exception": str(start_status.detail),
                        },
                    )
                    await self.show_message_to_client(
                        f"Cannot start a new Pyre server at `{self.server_identifier}`.",
                        level=lsp.MessageType.ERROR,
                    )

                    if (
                        self.server_state.consecutive_start_failure
                        == CONSECUTIVE_START_ATTEMPT_THRESHOLD - 1
                    ):
                        # The heuristic here is that if the restart has failed
                        # this many times, it is highly likely that the restart was
                        # blocked by a defunct socket file instead of a concurrent
                        # server start, in which case removing the file could unblock
                        # the server.
                        LOG.warning(f"Removing defunct socket file {socket_path}...")
                        stop.remove_socket_if_exists(socket_path)
                else:
                    await self.show_message_to_client(
                        (
                            f"Pyre server restart at `{self.server_identifier}` has been "
                            + "failing repeatedly. Disabling The Pyre plugin for now."
                        ),
                        level=lsp.MessageType.ERROR,
                    )
                    _log_lsp_event(
                        remote_logging=self.pyre_arguments.remote_logging,
                        event=LSPEvent.SUSPENDED,
                        normals=self._auxiliary_logging_info(),
                    )

            else:
                raise RuntimeError("Impossible type for `start_status`")

    async def run(self) -> None:
        try:
            await self._run()
        except Exception:
            _log_lsp_event(
                remote_logging=self.pyre_arguments.remote_logging,
                event=LSPEvent.DISCONNECTED,
                normals={
                    **self._auxiliary_logging_info(),
                    "exception": traceback.format_exc(),
                },
            )
            raise


class PysaServer:
    # I/O Channels
    input_channel: connection.TextReader
    output_channel: connection.TextWriter

    # Immutable States
    client_capabilities: lsp.ClientCapabilities

    # Mutable States
    state: ServerState
    pyre_manager: connection.BackgroundTaskManager

    def __init__(
        self,
        input_channel: connection.TextReader,
        output_channel: connection.TextWriter,
        client_capabilities: lsp.ClientCapabilities,
        state: ServerState,
        pyre_manager: connection.BackgroundTaskManager,
        # binary_location: str,
        # server_identifier: str,
        # pyre_arguments: start.Arguments,
        # client_output_channel: connection.TextWriter,
        # server_state: ServerState,
    ) -> None:
        self.input_channel = input_channel
        self.output_channel = output_channel
        self.client_capabilities = client_capabilities
        self.state = state
        self.pyre_manager = pyre_manager

    # async def update_errors() -> None:
    #     pyre_connection = api_connection.PyreConnection(Path(PysaServerHandler.pyre_arguments.global_root))
    #     initial_model_errors = query.get_invalid_taint_models(pyre_connection)
    #     PysaServerHandler.update_model_errors(initial_model_errors)
    #     await PysaServerHandler.show_model_errors_to_client()

    # async def show_message_to_client(
    #     self, message: str, level: lsp.MessageType = lsp.MessageType.INFO
    # ) -> None:
    #     await lsp.write_json_rpc(
    #         self.client_output_channel,
    #         json_rpc.Request(
    #             method="window/showMessage",
    #             parameters=json_rpc.ByNameParameters(
    #                 {"type": int(level), "message": message}
    #             ),
    #         ),
    #     )

    # async def log_and_show_message_to_client(
    #     self, message: str, level: lsp.MessageType = lsp.MessageType.INFO
    # ) -> None:
    #     if level == lsp.MessageType.ERROR:
    #         LOG.error(message)
    #     elif level == lsp.MessageType.WARNING:
    #         LOG.warning(message)
    #     elif level == lsp.MessageType.INFO:
    #         LOG.info(message)
    #     else:
    #         LOG.debug(message)
    #     await self.show_message_to_client(message, level)

    # # ** NEW **
    # # Updating model errors from Pysa
    # def update_model_errors(self, model_errors: Sequence[query.InvalidModel]) -> None:
    #     # LOG.info(
    #     #     (
    #     #         "Refreshing errors received from Pysa server. "
    #     #         + f"Total number of errors is {len(model_errors)}."
    #     #     )
    #     # )
    #     self.server_state.diagnostics = invalid_models_to_diagnostics(model_errors)
    #     # LOG.info(f"update_model_errors(): server_state.diagnostics = {self.server_state.diagnostics}")

    # # ** NEW **
    # # Show invalid model errors from Pysa to the client
    # async def show_model_errors_to_client(self) -> None:
    #     # LOG.info(f"server_state.opened_documents = {self.server_state.opened_documents}")
    #     for path in self.server_state.opened_documents:
    #         path_str = str(path.resolve())
    #     # path = "/home/momo/Documents/Programs/pyre-check/documentation/pysa_tutorial/exercise2/sources_sinks.pysa"
    #         await _publish_diagnostics(self.client_output_channel, path_str, [])
    #         diagnostics = self.server_state.diagnostics.get(path_str, None)
    #         LOG.info(f"show_model_errors_to_client(): diagnostics = {diagnostics}")
    #         if diagnostics is not None:
    #             await _publish_diagnostics(
    #                 self.client_output_channel, path_str, diagnostics
    #             )

    async def wait_for_exit(self) -> int:
        while True:
            async with _read_lsp_request(
                self.input_channel, self.output_channel
            ) as request:
                LOG.debug(f"Received post-shutdown request: {request}")

                if request.method == "exit":
                    return 0
                else:
                    raise json_rpc.InvalidRequestError("LSP server has been shut down")

    async def _try_restart_pyre_server(self) -> None:
        if self.state.consecutive_start_failure < CONSECUTIVE_START_ATTEMPT_THRESHOLD:
            await self.pyre_manager.ensure_task_running()
        else:
            LOG.info(
                (
                    "Not restarting Pysa since failed consecutive start attempt limit"
                    + " has been reached."
                )
            )

    async def process_open_request(
        self, parameters: lsp.DidOpenTextDocumentParameters
    ) -> None:
        # LOG.info("** Inside process_open_request() **")
        document_path = parameters.text_document.document_uri().to_file_path()
        if document_path is None:
            raise json_rpc.InvalidRequestError(
                f"Document URI is not a file: {parameters.text_document.uri}"
            )
        self.state.opened_documents.add(document_path)
        # LOG.info(f"File opened: {document_path}")

        await update_errors()
        # Attempt to trigger a background Pyre server start on each file open
        if not self.pyre_manager.is_task_running():
            await self._try_restart_pyre_server()
        else:
            document_diagnostics = self.state.diagnostics.get(document_path, None)
            if document_diagnostics is not None:
                LOG.info(f"Update diagnostics for {document_path}")
                await _publish_diagnostics(
                    self.output_channel, document_path, document_diagnostics
                )

    async def process_close_request(
        self, parameters: lsp.DidCloseTextDocumentParameters
    ) -> None:
        # LOG.info("** Inside process_close_request() **")
        document_path = parameters.text_document.document_uri().to_file_path()
        if document_path is None:
            raise json_rpc.InvalidRequestError(
                f"Document URI is not a file: {parameters.text_document.uri}"
            )
        try:
            self.state.opened_documents.remove(document_path)
            LOG.info(f"File closed: {document_path}")

            if document_path in self.state.diagnostics:
                LOG.info(f"Clear diagnostics for {document_path}")
                await _publish_diagnostics(self.output_channel, document_path, [])
        except KeyError:
            LOG.warning(f"Trying to close an un-opened file: {document_path}")

    async def process_did_save_request(
        self, parameters: lsp.DidSaveTextDocumentParameters
    ) -> None:
        # LOG.info("** Inside process_did_save_request() **")
        document_path = parameters.text_document.document_uri().to_file_path()
        if document_path is None:
            raise json_rpc.InvalidRequestError(
                f"Document URI is not a file: {parameters.text_document.uri}"
            )
        await update_errors()
        # Attempt to trigger a background Pyre server start on each file save
        if (
            not self.pyre_manager.is_task_running()
            and document_path in self.state.opened_documents
        ):
            await self._try_restart_pyre_server()

    async def _run(self) -> int:
        # LOG.info("** Inside _run function now. **")
        while True:
            async with _read_lsp_request(
                self.input_channel, self.output_channel
            ) as request:
                # LOG.info(f"Received LSP request: {request}")

                if request.method == "exit":
                    return commands.ExitCode.FAILURE
                elif request.method == "shutdown":
                    lsp.write_json_rpc(
                        self.output_channel,
                        json_rpc.SuccessResponse(id=request.id, result=None),
                    )
                    return await self.wait_for_exit()
                elif request.method == "textDocument/didOpen":
                    # LOG.info("** A Document just opened! **")
                    parameters = request.parameters
                    if parameters is None:
                        raise json_rpc.InvalidRequestError(
                            "Missing parameters for didOpen method"
                        )
                    await self.process_open_request(
                        lsp.DidOpenTextDocumentParameters.from_json_rpc_parameters(
                            parameters
                        )
                    )
                elif request.method == "textDocument/didClose":
                    # LOG.info("** A Document just closed! **")
                    parameters = request.parameters
                    if parameters is None:
                        raise json_rpc.InvalidRequestError(
                            "Missing parameters for didClose method"
                        )
                    await self.process_close_request(
                        lsp.DidCloseTextDocumentParameters.from_json_rpc_parameters(
                            parameters
                        )
                    )
                elif request.method == "textDocument/didSave":
                    # LOG.info("** A Document just saved! **")
                    parameters = request.parameters
                    if parameters is None:
                        raise json_rpc.InvalidRequestError(
                            "Missing parameters for didSave method"
                        )
                    await self.process_did_save_request(
                        lsp.DidSaveTextDocumentParameters.from_json_rpc_parameters(
                            parameters
                        )
                    )
                elif request.id is not None:
                    raise lsp.RequestCancelledError("Request not supported yet")

    async def run(self) -> int:
        # LOG.info("** Inside run function now. **")
        try:
            await self.pyre_manager.ensure_task_running()
            return await self._run()
        finally:
            await self.pyre_manager.ensure_task_stop()


async def run_persistent(
    binary_location: str, server_identifier: str, pysa_arguments: start.Arguments
) -> int:
    global PYSA_HANDLER_OBJ
    stdin, stdout = await connection.create_async_stdin_stdout()
    while True:
        initialize_result = await try_initialize(stdin, stdout)
        if isinstance(initialize_result, InitializationExit):
            LOG.info("Received exit request before initialization.")
            return 0
        elif isinstance(initialize_result, InitializationSuccess):
            LOG.info("Initialization successful.")
            client_info = initialize_result.client_info
            _log_lsp_event(
                remote_logging=pysa_arguments.remote_logging,
                event=LSPEvent.INITIALIZED,
                normals=(
                    {}
                    if client_info is None
                    else {
                        "lsp client name": client_info.name,
                        "lsp client version": client_info.version,
                    }
                ),
            )

            client_capabilities = initialize_result.client_capabilities
            LOG.debug(f"Client capabilities: {client_capabilities}")
            initial_server_state = ServerState()
            PYSA_HANDLER_OBJ = PysaServerHandler(
                binary_location=binary_location,
                server_identifier=server_identifier,
                pyre_arguments=pysa_arguments,
                client_output_channel=stdout,
                server_state=initial_server_state,
            )
            server = PysaServer(
                input_channel=stdin,
                output_channel=stdout,
                client_capabilities=client_capabilities,
                state=initial_server_state,
                # binary_location=binary_location,
                # server_identifier=server_identifier,
                # pyre_arguments=pysa_arguments,
                # client_output_channel=stdout,
                # server_state=initial_server_state,
                pyre_manager=connection.BackgroundTaskManager(PYSA_HANDLER_OBJ),
            )
            return await server.run()
        elif isinstance(initialize_result, InitializationFailure):
            exception = initialize_result.exception
            message = (
                str(exception) if exception is not None else "ignoring notification"
            )
            LOG.info(f"Initialization failed: {message}")
            # Loop until we get either InitializeExit or InitializeSuccess
        else:
            raise RuntimeError("Cannot determine the type of initialize_result")


def run(
    configuration: configuration_module.Configuration,
    start_arguments: command_arguments.StartArguments,
) -> int:
    binary_location = configuration.get_binary_respecting_override()
    if binary_location is None:
        raise configuration_module.InvalidConfiguration(
            "Cannot locate a Pyre binary to run."
        )

    server_identifier = start.get_server_identifier(configuration)
    pyre_arguments = start.create_server_arguments(configuration, start_arguments)
    if pyre_arguments.watchman_root is None:
        raise commands.ClientException(
            (
                "Cannot locate a `watchman` root. Pyre's server will not function "
                + "properly."
            )
        )

    return asyncio.get_event_loop().run_until_complete(
        run_persistent(binary_location, server_identifier, pyre_arguments)
    )
