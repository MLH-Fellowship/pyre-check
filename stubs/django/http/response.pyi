# Copyright (c) Facebook, Inc. and its affiliates.
#
# This source code is licensed under the MIT license found in the
# LICENSE file in the root directory of this source tree.

# pyre-unsafe

from typing import Any, Dict, Iterable, Iterator, List, Optional, Union, overload

class HttpResponseBase(Iterator[bytes]):
    status_code: int
    def __init__(self, content_type=..., status=..., reason=..., charset=...): ...
    _headers: Dict[str, str]
    reason_phrase: str
    cookies: Any = ...
    charset: str = ...
    def serialize_headers(self) -> bytes: ...
    __bytes__ = serialize_headers
    def set_cookie(
        self,
        key: str,
        value: str = ...,
        max_age: Any = ...,
        expires: Any = ...,
        path: str = ...,
        domain: Any = ...,
        secure: bool = ...,
        httponly: bool = ...,
    ) -> None: ...
    def set_signed_cookie(
        self, key: str, value: Any, salt: str = ..., **kwargs
    ) -> None: ...
    def delete_cookie(self, key: str, path: Optional[str] = ..., domain: Any = ...): ...
    streaming: bool = ...
    # We explicitly override __next__ here to avoid abstract class instantiation errors.
    def __next__(self) -> bytes: ...
    def items(self) -> List[str]: ...
    @overload
    def get(self, header: str, alternate: str = ...) -> str: ...
    @overload
    def get(self, header: str, alternate: Optional[str] = ...) -> Optional[str]: ...
    def __getitem__(self, header: str) -> str: ...
    def __setitem__(self, header: str, value: Union[str, int]) -> None: ...
    def setdefault(self, header: str, value: Union[int, str]) -> None: ...
    def has_header(self, header: str) -> bool: ...

class HttpResponse(HttpResponseBase):
    _slipstream_error_name: Any = ...
    def __init__(self, content=..., *args, **kwargs): ...
    def __repr__(self) -> str: ...
    def serialize(self) -> bytes: ...
    __bytes__ = serialize
    @property
    def content(self) -> bytes: ...
    @content.setter
    def content(self, value: Any) -> bytes: ...
    def __iter__(self): ...
    def write(self, content): ...
    def tell(self) -> int: ...
    def getvalue(self) -> bytes: ...

class HttpResponseRedirect(HttpResponse):
    def __init__(self, redirect_to, *args, **kwargs): ...
    url: Any = ...

class HttpResponsePermanentRedirect(HttpResponseRedirect): ...
class Http404(Exception): ...
class HttpResponseForbidden(HttpResponse): ...
class HttpResponseBadRequest(HttpResponse): ...
class HttpResponseNotAllowed(HttpResponse): ...
class HttpResponseNotFound(HttpResponse): ...
class HttpResponseServerError(HttpResponse): ...

class StreamingHttpResponse(HttpResponseBase):
    def __init__(
        self,
        streaming_content=Iterable[bytes],
        content_type=...,
        status=...,
        reason=...,
        charset=...,
    ): ...
    streaming_content: Iterable[bytes]

class FileResponse(StreamingHttpResponse):
    block_size: int
