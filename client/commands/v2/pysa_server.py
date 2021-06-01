# Copyright (c) Facebook, Inc. and its affiliates.
#
# This source code is licensed under the MIT license found in the
# LICENSE file in the root directory of this source tree.


"""
This is an implementation of Pysa's language server. It is a refactored
version of persistent.py.
"""

from pathlib import Path
import asyncio
import logging

from ... import (
    json_rpc,
    command_arguments,
    commands,
    configuration as configuration_module,
)
from . import (
    language_server_protocol as lsp,
    async_server_connection as connection,
    start,
)
from .persistent import (
    LSPEvent,
    _read_lsp_request,
    try_initialize,
    _log_lsp_event,
    InitializationExit,
    InitializationSuccess,
    InitializationFailure,
)
from api import query, connection as api_connection
from api.connection import PyreQueryError
from typing import List, Sequence, Dict

LOG: logging.Logger = logging.getLogger(__name__)


class PysaServer:
    # I/O Channels
    input_channel: connection.TextReader
    output_channel: connection.TextWriter

    # Immutable States
    client_capabilities: lsp.ClientCapabilities

    def __init__(
        self,
        input_channel: connection.TextReader,
        output_channel: connection.TextWriter,
        client_capabilities: lsp.ClientCapabilities,
        pyre_arguments: start.Arguments,
        binary_location: str,
        server_identifier: str,
    ) -> None:
        self.input_channel = input_channel
        self.output_channel = output_channel
        self.client_capabilities = client_capabilities
        self.pyre_arguments = pyre_arguments
        self.binary_location = binary_location
        self.server_identifier = server_identifier

    def invalid_model_to_diagnostic(
        self,
        invalid_model: query.InvalidModel,
    ) -> lsp.Diagnostic:
        return lsp.Diagnostic(
            range=lsp.Range(
                start=lsp.Position(
                    line=invalid_model.line - 1, character=invalid_model.column
                ),
                end=lsp.Position(
                    line=invalid_model.stop_line - 1,
                    character=invalid_model.stop_column,
                ),
            ),
            message=invalid_model.full_error_message,
            severity=lsp.DiagnosticSeverity.ERROR,
            code=None,
            source="Pysa",
        )

    def invalid_models_to_diagnostics(
        self,
        invalid_models: Sequence[query.InvalidModel],
    ) -> Dict[Path, List[lsp.Diagnostic]]:
        result: Dict[Path, List[lsp.Diagnostic]] = {}
        for model in invalid_models:
            result.setdefault(Path(model.path), []).append(
                self.invalid_model_to_diagnostic(model)
            )
        return result

    async def update_errors(self, document_path: Path) -> None:
        await _publish_diagnostics(self.output_channel, document_path, [])
        pyre_connection = api_connection.PyreConnection(
            Path(self.pyre_arguments.global_root)
        )
        try:
            model_errors = query.get_invalid_taint_models(pyre_connection)
            diagnostics = self.invalid_models_to_diagnostics(model_errors)
            await self.show_model_errors_to_client(diagnostics)
        except PyreQueryError as e:
            await self.log_and_show_message_to_client(
                f"Error querying Pyre: {e}", lsp.MessageType.WARNING
            )

    async def show_message_to_client(
        self, message: str, level: lsp.MessageType = lsp.MessageType.INFO
    ) -> None:
        await lsp.write_json_rpc(
            self.output_channel,
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

    async def show_model_errors_to_client(
        self, diagnostics: Dict[Path, List[lsp.Diagnostic]]
    ) -> None:
        for path, diagnostic in diagnostics.items():
            await _publish_diagnostics(self.output_channel, path, [])
            if diagnostic is not None:
                await _publish_diagnostics(self.output_channel, path, diagnostic)

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

    async def process_open_request(
        self, parameters: lsp.DidOpenTextDocumentParameters
    ) -> None:
        document_path = parameters.text_document.document_uri().to_file_path()
        if document_path is None:
            raise json_rpc.InvalidRequestError(
                f"Document URI is not a file: {parameters.text_document.uri}"
            )
        await self.update_errors(document_path)

    async def process_close_request(
        self, parameters: lsp.DidCloseTextDocumentParameters
    ) -> None:
        document_path = parameters.text_document.document_uri().to_file_path()
        if document_path is None:
            raise json_rpc.InvalidRequestError(
                f"Document URI is not a file: {parameters.text_document.uri}"
            )
        try:
            LOG.info(f"File closed: {document_path}")

        except KeyError:
            LOG.warning(f"Trying to close an un-opened file: {document_path}")

    async def process_did_save_request(
        self, parameters: lsp.DidSaveTextDocumentParameters
    ) -> None:
        document_path = parameters.text_document.document_uri().to_file_path()
        if document_path is None:
            raise json_rpc.InvalidRequestError(
                f"Document URI is not a file: {parameters.text_document.uri}"
            )
        await self.update_errors(document_path)

    async def run(self) -> int:
        while True:
            async with _read_lsp_request(
                self.input_channel, self.output_channel
            ) as request:

                if request.method == "exit":
                    return commands.ExitCode.FAILURE
                elif request.method == "shutdown":
                    lsp.write_json_rpc(
                        self.output_channel,
                        json_rpc.SuccessResponse(id=request.id, result=None),
                    )
                    return await self.wait_for_exit()
                elif request.method == "textDocument/didOpen":
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


async def run_persistent(
    binary_location: str, server_identifier: str, pysa_arguments: start.Arguments
) -> int:
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
            server = PysaServer(
                input_channel=stdin,
                output_channel=stdout,
                client_capabilities=client_capabilities,
                binary_location=binary_location,
                server_identifier=server_identifier,
                pyre_arguments=pysa_arguments,
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
