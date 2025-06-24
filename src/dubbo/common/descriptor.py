#
# Licensed to the Apache Software Foundation (ASF) under one or more
# contributor license agreements.  See the NOTICE file distributed with
# this work for additional information regarding copyright ownership.
# The ASF licenses this file to You under the Apache License, Version 2.0
# (the "License"); you may not use this file except in compliance with
# the License.  You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
import enum
import inspect
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Optional, Union

from .classes import CallType

__all__ = ["ParamKind", "ParamDetail", "ReturnParamDetail", "MethodDescriptor", "get_method_descriptor"]


@enum.unique
class ParamKind(enum.StrEnum):
    """
    Enumeration for parameter kinds in method signatures.
    Attributes:
        POSITIONAL_ONLY: Parameter that can only be passed positionally.
        POSITIONAL_OR_KEYWORD: Parameter that can be passed either positionally or as a keyword.
        KEYWORD_ONLY: Parameter that can only be passed as a keyword argument.
        VAR_POSITIONAL: Variable number of positional arguments (e.g., *args).
        VAR_KEYWORD: Variable number of keyword arguments (e.g., **kwargs).
        RETURN: Special kind for return parameters.
    """

    POSITIONAL_ONLY = "positional_only"
    POSITIONAL_OR_KEYWORD = "positional_or_keyword"
    KEYWORD_ONLY = "keyword_only"
    VAR_POSITIONAL = "var_positional"
    VAR_KEYWORD = "var_keyword"
    RETURN = "return"


# Mapping from inspect.Parameter kinds to ParamKind
_INSPECTION_TO_KIND = {
    inspect.Parameter.POSITIONAL_ONLY: ParamKind.POSITIONAL_ONLY,
    inspect.Parameter.POSITIONAL_OR_KEYWORD: ParamKind.POSITIONAL_OR_KEYWORD,
    inspect.Parameter.KEYWORD_ONLY: ParamKind.KEYWORD_ONLY,
    inspect.Parameter.VAR_POSITIONAL: ParamKind.VAR_POSITIONAL,
    inspect.Parameter.VAR_KEYWORD: ParamKind.VAR_KEYWORD,
}


@dataclass
class ParamDetail:
    """
    Represents metadata about a function parameter.

    NOTE:
        You should never check if a parameter is required using `default == None`.
        Instead, always rely on the `required` flag.

    Attributes:
        name (str): The name of the parameter.
        annotation (Any): The type annotation of the parameter. Can be a basic type or Annotated type.
        kind (str): The kind of parameter (e.g., 'positional', 'keyword').
        required (bool): Whether the parameter is required (i.e., no default value is provided).
        default (Any): The default value of the parameter, if provided.
    """

    name: str
    annotation: Any
    kind: ParamKind
    required: bool = True
    default: Any = None


@dataclass
class ReturnParamDetail(ParamDetail):
    """
    Represents metadata about a function return value.

    Attributes:
        name (str): The name of the return value, typically 'return'.
        annotation (Any): The type annotation of the return value.
        kind (str): The kind of return value, typically 'return'.
        required (bool): Whether the return value is required.
        default (Any): The default value of the return, if applicable.
    """

    name: str = field(init=False, default="return")
    kind: ParamKind = field(init=False, default=ParamKind.RETURN)


@dataclass
class MethodDescriptor:
    """
    Represents metadata about a method, including its signature and call semantics.

    Attributes:
        name (str): The name of the method.
        call (Callable): The actual callable function.
        call_type (CallType): Indicates whether the method is unary, streaming, etc.
        params (list[ParamDetail]): Metadata about the method's input parameters.
        return_param (ReturnParamDetail): Metadata about the method's return value.
        attributes (dict[str, Any]): Additional attributes or metadata associated with the method.
    """

    name: str
    call: Optional[Callable[..., Any]]
    call_type: CallType
    params: list[ParamDetail]
    return_param: ReturnParamDetail
    attributes: dict[str, Any]


def get_method_descriptor(
    call_type: CallType,
    func: Optional[Callable[..., Any]] = None,
    *,
    name: Optional[str] = None,
    param_types: Union[type, list[type], dict[str, type], None] = None,
    return_type: Optional[type] = None,
    attributes: Optional[dict[str, Any]] = None,
) -> MethodDescriptor:
    """
    Extract metadata from a function or parameter definition to build a MethodDescriptor.

    Args:
        call_type: The call type indicating the kind of method invocation.
        func: Optional callable to infer parameter and return types.
        name: The method name. If not provided, inferred from func.__name__.
        param_types: Parameter types. Can be a single type, a list of types, or a dict of name->type.
        return_type: Return type annotation, overrides func's return annotation if provided.
        attributes: Optional dictionary of extra attributes.

    Returns:
        A MethodDescriptor instance describing the method.

    Raises:
        ValueError: If the method name is missing or parameter count mismatch.
        TypeError: If func contains unsupported *args or **kwargs parameters.
    """

    name = name or (func.__name__ if func else None)
    if not name:
        raise ValueError("Method name must be provided or inferable from the function's __name__.")

    params_dict: Optional[dict[str, type]] = _get_params_dict(param_types)
    params_details: Optional[list[ParamDetail]] = None

    if func:
        sig = inspect.signature(func)

        if params_dict is not None and len(params_dict) != len(sig.parameters):
            raise ValueError(
                f"Method '{name}' parameter count mismatch: "
                f"function defines {len(sig.parameters)} parameters, "
                f"but {len(params_dict)} parameter types were provided."
            )
        else:
            # Extract parameter details from the function signature
            params_details = [
                ParamDetail(
                    name=param.name,
                    annotation=param.annotation if param.annotation is not inspect.Parameter.empty else Any,
                    kind=_INSPECTION_TO_KIND[param.kind],
                    required=param.default is inspect.Parameter.empty,
                    default=param.default if param.default is not inspect.Parameter.empty else None,
                )
                for param in sig.parameters.values()
            ]

        if return_type is None:
            # Get the return type annotation from the function signature
            return_type = sig.return_annotation if sig.return_annotation is not inspect.Signature.empty else Any

    if params_details is None:
        if params_dict is None:
            raise ValueError("Must provide either 'param_types' or a 'func' to infer parameter details.")

        # If params is a list or a single type, treat it as positional parameters
        # If params is a dict, treat it as positional or keyword parameters
        kind = ParamKind.POSITIONAL_OR_KEYWORD if isinstance(params_dict, dict) else ParamKind.POSITIONAL_ONLY

        params_details = [
            ParamDetail(
                name=name,
                annotation=annotation,
                kind=kind,
                required=True,  # All provided params are considered required
                default=None,  # No default value provided
            )
            for name, annotation in params_dict.items()
        ]

    # validate parameter kinds for uniformity
    _validate_param_kinds_uniformity(params_details)

    return_param_detail = ReturnParamDetail(annotation=return_type if return_type is not None else Any)

    return MethodDescriptor(
        name=name,
        call=func,
        call_type=call_type,
        params=params_details,
        return_param=return_param_detail,
        attributes=attributes or {},
    )


def _get_params_dict(params: Union[type, list[type], dict[str, type], None]) -> Optional[dict[str, type]]:
    """
    Normalize various parameter formats into a dict of parameter names to types.

    Supported formats:
      - dict[str, type]: returned as is
      - list[type]: parameter names generated as param_0, param_1, ...
      - single type: parameter name generated as param_0
      - None: returns None

    Args:
        params: Parameter type(s) in various supported formats.

    Returns:
        A dictionary mapping parameter names to types, or None if input is None.
    """
    if isinstance(params, dict):
        return params
    if isinstance(params, list):
        return {f"param_{i}": p for i, p in enumerate(params)}
    if params is not None:
        return {"param_0": params}
    return None


def _validate_param_kinds_uniformity(params: list[ParamDetail]) -> None:
    """
    Ensure parameter kinds are consistent and only use supported kinds.

    Args:
        params: List of ParamDetail objects.

    Raises:
        TypeError: If unsupported or inconsistent parameter kinds are found.
    """
    allowed_kinds = {
        ParamKind.POSITIONAL_ONLY,
        ParamKind.POSITIONAL_OR_KEYWORD,
        ParamKind.KEYWORD_ONLY,
    }

    kinds_used = {param.kind for param in params}
    if not kinds_used.issubset(allowed_kinds):
        unsupported = kinds_used - allowed_kinds
        raise TypeError(
            f"Unsupported parameter kind(s): {unsupported}. Only positional and keyword parameters are allowed."
        )

    if ParamKind.POSITIONAL_ONLY in kinds_used and ParamKind.KEYWORD_ONLY in kinds_used:
        raise TypeError(
            "Cannot mix positional-only and keyword-only parameters in the same method. "
            "Please use either positional or keyword parameters consistently."
        )
