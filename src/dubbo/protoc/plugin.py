# Licensed to the Apache Software Foundation (ASF) under one
# or more contributor license agreements. See the NOTICE file
# distributed with this work for additional information
# regarding copyright ownership. The ASF licenses this file
# to you under the Apache License, Version 2.0 (the
# "License"); you may not use this file except in compliance
# with the License. You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing,
# software distributed under the License is distributed on an
# "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY
# KIND, either express or implied. See the License for the
# specific language governing permissions and limitations
# under the License.

"""The protoc plugin ``protoc-gen-python-dubbo``, which generates the Dubbo service contracts of proto files.

``protoc --python_out=. --pyi_out=. --python-dubbo_out=. greeter.proto``,
or ``python -m grpc_tools.protoc`` with the same options after installing
``apache-dubbo[protoc]``, writes ``greeter_dubbo.py`` beside ``greeter_pb2.py``. For each
service, named ``{package}.{Service}`` as in every language, it holds two
contracts: ``{Service}``, with ``_`` after a Python keyword, a
``typing.Protocol`` that callers refer to, and
``{Service}Servicer``, an abstract class that implementations subclass.
Their methods take Python names, such as ``say_hello`` for ``SayHello``,
and the message classes of the ``_pb2`` modules, imported by their
absolute names, as protoc names them.

A unary method annotated with ``google.api.http`` takes its routes, the
rule and each of its ``additional_bindings``, as `dubbo.route` declares
them, with the ``body`` and ``response_body`` of each. A path template
takes literal segments and variables of one segment, ``{field}``,
``{field.inner}`` or ``{field=*}``; one with another pattern, such as
``{name=shelves/*}``, ``*`` or ``**`` outside a variable, or a ``:verb``
suffix, fails the generation. The rule of a streaming method is left
out, as routes take unary methods alone. A method with the option
``deprecated`` is marked with ``typing_extensions.deprecated``, PEP 702,
which is ``warnings.deprecated`` from Python 3.13.
"""

import keyword
import re
import sys
from collections import Counter
from collections.abc import Callable, Iterable, Mapping
from typing import Any

from google.protobuf import descriptor_pool, message_factory
from google.protobuf.compiler.plugin_pb2 import CodeGeneratorRequest, CodeGeneratorResponse
from google.protobuf.descriptor_pb2 import FileDescriptorProto, MethodDescriptorProto, ServiceDescriptorProto

from dubbo.rpc.rest import path_segments

__all__ = ["generate", "main"]

# Returns the Python expression of a message class from its full name, as in ``.pkg.Message``.
_Reference = Callable[[str], str]
# Returns the google.api.http rule of a method, if it has one.
_Rules = Callable[[MethodDescriptorProto], Any]
_HTTP_FILES = ("google/protobuf/descriptor.proto", "google/api/http.proto", "google/api/annotations.proto")
# A variable of a path template: its field path, and the pattern after "=" when there is one.
_VARIABLE = re.compile(r"\{([^{}=]*)(?:=([^{}]*))?\}")
_FIELD_PATH = re.compile(r"[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*")
_VERBS = ("get", "put", "post", "delete", "patch")
# Where a lowercase letter or digit meets an uppercase one, or an acronym meets the next word, as in GetHTTPStatus.
_WORD_BOUNDARY = re.compile(r"(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])")


def main() -> None:
    """Read a ``CodeGeneratorRequest`` from standard input and write the ``CodeGeneratorResponse`` to standard output."""
    request = CodeGeneratorRequest.FromString(sys.stdin.buffer.read())
    sys.stdout.buffer.write(generate(request).SerializeToString())


def generate(request: CodeGeneratorRequest) -> CodeGeneratorResponse:
    """Return the ``_dubbo.py`` module of each file of ``request`` to generate that has services.

    A file whose services cannot become contracts, such as one whose
    methods share a Python name, sets the error of the response instead.
    """
    response = CodeGeneratorResponse(supported_features=CodeGeneratorResponse.FEATURE_PROTO3_OPTIONAL)
    files = {file.name: file for file in request.proto_file}
    messages = _messages(request.proto_file)
    rules = _http_rules(files)
    for name in request.file_to_generate:
        file = files[name]
        if not file.service:
            continue
        try:
            content = _module(file, messages, rules)
        except ValueError as error:
            response.error = f"{name}: {error}"
            return response
        module = _pb2_module(name).removesuffix("_pb2")
        response.file.add(name=module.replace(".", "/") + "_dubbo.py", content=content)

    return response


def _messages(files: Iterable[FileDescriptorProto]) -> dict[str, tuple[str, str]]:
    """Return the file and the Python name, such as ``Outer.Inner``, of each message by its full name, as in ``.pkg.Outer.Inner``."""
    messages = {}
    for file in files:
        prefix = f".{file.package}." if file.package else "."
        pending = [(message, message.name) for message in file.message_type]
        while pending:
            message, name = pending.pop()
            messages[prefix + name] = (file.name, name)
            pending.extend((nested, f"{name}.{nested.name}") for nested in message.nested_type)

    return messages


def _http_rules(files: Mapping[str, FileDescriptorProto]) -> _Rules:
    """Return what reads the ``google.api.http`` rule of a method, from the descriptors protoc sends with the request.

    The rule is an extension of the method options, which stays unparsed
    until the descriptor of ``google/api/annotations.proto`` is known; a
    request whose files do not import it has no rules.
    """
    if not all(name in files for name in _HTTP_FILES):
        return lambda method: None

    # A pool of its own keeps these types apart from any that the default pool holds.
    pool = descriptor_pool.DescriptorPool()
    for name in _HTTP_FILES:
        pool.Add(files[name])
    options_type = message_factory.GetMessageClassesForFiles(_HTTP_FILES, pool)["google.protobuf.MethodOptions"]
    extension: Any = pool.FindExtensionByName("google.api.http")

    def rule(method: MethodDescriptorProto) -> Any:
        options = options_type.FromString(method.options.SerializeToString())
        return options.Extensions[extension] if options.HasExtension(extension) else None

    return rule


def _module(file: FileDescriptorProto, messages: Mapping[str, tuple[str, str]], rules: _Rules) -> str:
    used = {
        _pb2_module(messages[name][0])
        for service in file.service
        for method in service.method
        for name in (method.input_type, method.output_type)
    }
    aliases = _aliases(used)
    deprecates = any(method.options.deprecated for service in file.service for method in service.method)
    # The names the module imports, which a contract or a method of the same name would hide.
    imported = {
        "abc",
        "typing",
        "dubbo",
        *aliases.values(),
        *(("deprecated",) if deprecates else ()),
        *(("importlib",) if any(_has_keyword(module) for module in aliases) else ()),
    }

    names = [name for service in file.service for name in (_class_name(service.name), f"{service.name}Servicer")]
    if len(set(names)) < len(names):
        raise ValueError("the contracts of two services share a Python name")
    if hidden := sorted(imported.intersection(names)):
        raise ValueError(f"a contract would hide the imported name {hidden[0]!r}")
    exported = ", ".join(f'"{name}"' for name in names)

    def reference(name: str) -> str:
        proto, qualified = messages[name]
        expression = aliases[_pb2_module(proto)]
        for part in qualified.split("."):
            expression = f'getattr({expression}, "{part}")' if keyword.iskeyword(part) else f"{expression}.{part}"
        return expression

    comments = {tuple(location.path): location.leading_comments for location in file.source_code_info.location}
    lines = [
        "# Generated by protoc-gen-python-dubbo. DO NOT EDIT!",
        f"# source: {file.name}",
        f'"""Dubbo service contracts of {file.name}."""',
        "",
        "import abc",
        "import typing",
        "",
        "import dubbo",
        *(("from typing_extensions import deprecated",) if deprecates else ()),
        "",
    ]
    # A module under a package named with a keyword, such as from/catalog.proto, cannot be named in an import.
    plain = {module: alias for module, alias in aliases.items() if not _has_keyword(module)}
    loaded = {module: alias for module, alias in aliases.items() if _has_keyword(module)}
    if loaded:
        lines.insert(lines.index("import abc") + 1, "import importlib")
    if plain:
        lines += [*sorted(_import(module, alias) for module, alias in plain.items()), ""]
    if loaded:
        lines += [*(f'{alias} = importlib.import_module("{module}")' for module, alias in sorted(loaded.items())), ""]
    lines.append(f"__all__ = [{exported}]")
    for index, service in enumerate(file.service):
        lines += ["", "", *_contracts(file, index, service, comments, reference, imported, rules)]

    return "\n".join(lines) + "\n"


def _pb2_module(proto: str) -> str:
    return proto.removesuffix(".proto").replace("-", "_").replace("/", ".") + "_pb2"


def _aliases(modules: Iterable[str]) -> dict[str, str]:
    """Return the name each ``_pb2`` module is imported as: its own, unless two modules share it."""
    modules = sorted(modules)
    counts = Counter(module.rpartition(".")[2] for module in modules)
    aliases = {}
    for module in modules:
        name = module.rpartition(".")[2]
        aliases[module] = name if counts[name] == 1 else module.replace("_", "__").replace(".", "_dot_")

    return aliases


def _has_keyword(module: str) -> bool:
    return any(keyword.iskeyword(part) for part in module.split("."))


def _class_name(service: str) -> str:
    """Return the Python name of the caller's contract of ``service``: its name, with ``_`` after a keyword."""
    return f"{service}_" if keyword.iskeyword(service) else service


def _import(module: str, alias: str) -> str:
    package, _, name = module.rpartition(".")
    statement = f"from {package} import {name}" if package else f"import {name}"
    return statement if alias == name else f"{statement} as {alias}"


def _contracts(
    file: FileDescriptorProto,
    index: int,
    service: ServiceDescriptorProto,
    comments: Mapping[tuple[int, ...], str],
    reference: _Reference,
    imported: set[str],
    rules: _Rules,
) -> list[str]:
    """Return the lines of the caller's contract of ``service``, then of its servicer."""
    path = (FileDescriptorProto.SERVICE_FIELD_NUMBER, index)
    interface = f"{file.package}.{service.name}" if file.package else service.name
    docstring = _docstring(comments.get(path), "    ")
    caller = [f'@dubbo.service("{interface}")', f"class {_class_name(service.name)}(typing.Protocol):", *docstring]
    servicer = [f'@dubbo.service("{interface}")', f"class {service.name}Servicer(abc.ABC):", *docstring]
    if not service.method and not docstring:
        caller.append("    ...")
        servicer.append("    ...")

    attributes: set[str] = set()
    for number, method in enumerate(service.method):
        attribute = _attribute(method.name)
        if attribute.startswith("_"):
            raise ValueError(f"method {service.name}.{method.name} would be private in Python")
        if attribute in attributes:
            raise ValueError(f"methods of {service.name} share the Python name {attribute!r}")
        if attribute in imported:
            raise ValueError(f"method {service.name}.{method.name} would hide the imported name {attribute!r}")
        attributes.add(attribute)

        comment = _docstring(comments.get((*path, ServiceDescriptorProto.METHOD_FIELD_NUMBER, number)), " " * 8)
        request, response = reference(method.input_type), reference(method.output_type)
        # The decorators both contracts share: the name, the routes, and whether the method is deprecated.
        decorators = [f'    @dubbo.method("{method.name}")']
        rule = rules(method)
        if rule is not None and not (method.client_streaming or method.server_streaming):
            try:
                decorators += _routes(rule)
            except ValueError as error:
                raise ValueError(f"the google.api.http rule of {service.name}.{method.name}: {error}") from None
        if method.options.deprecated:
            decorators.append(f'    @deprecated("{interface}.{method.name} is deprecated")')
        caller += ["", *decorators]
        caller += _body(_signature(attribute, *_called(method, request, response)), comment)
        servicer += ["", *decorators, "    @abc.abstractmethod"]
        servicer += _body(_signature(attribute, *_served(method, request, response)), comment)

    return [*caller, "", "", *servicer]


def _routes(rule: Any) -> list[str]:
    """Return the decorators of the routes of the ``google.api.http`` ``rule``: its own, then its additional bindings.

    Raises:
        ValueError: A binding has no pattern, a path template that a route
            cannot take, or additional bindings of its own.
    """
    decorators = [_route(rule)]
    for binding in rule.additional_bindings:
        if len(binding.additional_bindings):
            raise ValueError("an additional binding has additional bindings of its own")
        decorators.append(_route(binding))
    return decorators


def _route(rule: Any) -> str:
    """Return the decorator of the route of one binding of a ``google.api.http`` rule."""
    pattern = rule.WhichOneof("pattern")
    if pattern is None:
        raise ValueError("a binding names no HTTP method and path")
    if pattern == "custom":
        custom = rule.custom
        call = f"dubbo.route({_route_path(custom.path)!r}, methods=[{custom.kind.upper()!r}]"
    else:
        call = f"dubbo.{pattern}({_route_path(getattr(rule, pattern))!r}"
    # google.api.http leaves out the body by default, where a route takes the whole message.
    body = rule.body
    if body != "*":
        call += f", body={body or None!r}"
    if response_body := rule.response_body:
        call += f", response_body={response_body!r}"
    return f"    @{call})"


def _route_path(template: str) -> str:
    """Return the route path of the ``google.api.http`` path ``template``: ``{field}`` for each variable.

    Raises:
        ValueError: The template has a variable of more than one segment, a
            wildcard outside a variable, a ``:verb``, or is otherwise no
            route path.
    """
    # A ":" after the last "/" outside a variable starts a verb.
    if ":" in _VARIABLE.sub("", template).rpartition("/")[2]:
        raise ValueError(f"{template!r} ends with a :verb, which a route cannot take")

    def variable(match: re.Match[str]) -> str:
        field, segments = match.groups()
        if not _FIELD_PATH.fullmatch(field):
            raise ValueError(f"{template!r} names {field!r}, which is no field path")
        if segments not in (None, "*"):
            raise ValueError(f"{template!r} binds {field!r} to {segments!r}; a route takes one segment alone")
        return "{" + field + "}"

    path = _VARIABLE.sub(variable, template)
    if any(segment in ("*", "**") for segment in path.split("/")):
        raise ValueError(f"{template!r} has a wildcard outside a variable, which a route cannot take")
    try:
        path_segments(path)
    except ValueError as error:
        raise ValueError(str(error)) from None
    return path


def _called(method: MethodDescriptorProto, request: str, response: str) -> tuple[str, list[str], str]:
    """Return the keyword, the parameters and the return type of ``method`` as callers hold it."""
    match method.client_streaming, method.server_streaming:
        case False, False:
            return "async def", ["self", f"request: {request}"], response
        case False, True:
            return "def", ["self", f"request: {request}"], f"dubbo.ServerStream[{response}]"
        case True, False:
            return "def", ["self"], f"dubbo.ClientStream[{request}, {response}]"
        case _:
            return "def", ["self"], f"dubbo.BidiStream[{request}, {response}]"


def _served(method: MethodDescriptorProto, request: str, response: str) -> tuple[str, list[str], str]:
    """Return the keyword, the parameters and the return type of ``method`` as implementations serve it."""
    match method.client_streaming, method.server_streaming:
        case False, False:
            return "async def", ["self", f"request: {request}"], response
        case False, True:
            return "async def", ["self", f"request: {request}", f"stream: dubbo.SendStream[{response}]"], "None"
        case True, False:
            return "async def", ["self", f"stream: dubbo.ReceiveStream[{request}]"], response
        case _:
            return "async def", ["self", f"stream: dubbo.Stream[{response}, {request}]"], "None"


def _signature(attribute: str, keyword: str, parameters: list[str], returns: str) -> str:
    return f"    {keyword} {attribute}({', '.join(parameters)}) -> {returns}:"


def _body(signature: str, docstring: list[str]) -> list[str]:
    return [signature, *docstring] if docstring else [f"{signature} ..."]


def _attribute(name: str) -> str:
    """Return the Python name of the method ``name``: in snake case, with ``_`` after a keyword."""
    attribute = _WORD_BOUNDARY.sub("_", name).lower()
    return f"{attribute}_" if keyword.iskeyword(attribute) else attribute


def _docstring(comment: str | None, indent: str) -> list[str]:
    """Return the lines of a docstring holding the proto comment ``comment``, if any."""
    if not comment or not comment.strip():
        return []

    text = comment.replace("\\", "\\\\").strip("\n").rstrip()
    if text.endswith('"'):
        text = text[:-1] + '\\"'
    text = text.replace('"""', '\\"\\"\\"')
    lines = [line.removeprefix(" ").rstrip() for line in text.split("\n")]
    if len(lines) == 1:
        return [f'{indent}"""{lines[0].strip()}"""']
    return [
        f'{indent}"""{lines[0].strip()}',
        *(f"{indent}{line}" if line else "" for line in lines[1:]),
        f'{indent}"""',
    ]
