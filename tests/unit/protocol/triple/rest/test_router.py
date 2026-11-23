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

import pytest

from dubbo.protocol.triple.rest.router import Found, RestRouter, RouteEntry, WrongMethod


def _endpoint(path: str, *methods: str, group: str = "", version: str = "") -> RouteEntry:
    return RouteEntry(path=path, methods=frozenset(methods or ("GET",)), group=group, version=version, target=path)


def _segments(path: str) -> list[str]:
    return path[1:].split("/")


def _find(router: RestRouter, path: str, method: str = "GET", group: str | None = None, version: str | None = None):
    found = router.find(method, _segments(path), group, version)
    assert isinstance(found, Found)
    return found.endpoint.target, found.variables


def test_text_beats_a_segment_variable_beats_the_rest_whatever_the_order() -> None:
    router = RestRouter(
        [_endpoint("/files/{rest:path}"), _endpoint("/users/{id}"), _endpoint("/users/me"), _endpoint("/{a}/{b}")]
    )

    assert _find(router, "/users/me") == ("/users/me", {})
    assert _find(router, "/users/42") == ("/users/{id}", {"id": "42"})
    assert _find(router, "/files/a/b.txt") == ("/files/{rest:path}", {"rest": "a/b.txt"})
    assert _find(router, "/files/x") == ("/files/{rest:path}", {"rest": "x"})
    assert _find(router, "/teams/7") == ("/{a}/{b}", {"a": "teams", "b": "7"})


def test_a_text_match_that_lacks_the_method_falls_back_to_a_template() -> None:
    router = RestRouter([_endpoint("/users/me", "GET"), _endpoint("/users/{id}", "DELETE")])

    assert _find(router, "/users/me", "DELETE") == ("/users/{id}", {"id": "me"})


def test_a_path_routed_for_other_methods_is_not_allowed() -> None:
    router = RestRouter([_endpoint("/users/{id}", "GET", "PUT")])

    assert router.find("POST", ["users", "1"], None, None) == WrongMethod(frozenset({"GET", "PUT"}))
    assert router.find("GET", ["teams", "1"], None, None) is None


def test_a_trailing_slash_and_empty_segments_are_handled() -> None:
    router = RestRouter([_endpoint("/"), _endpoint("/users"), _endpoint("/users/{id}")])

    assert _find(router, "/")[0] == "/"
    assert _find(router, "/users/")[0] == "/users"
    assert router.find("GET", _segments("/users//x"), None, None) is None


@pytest.mark.parametrize(
    ("group", "version", "chosen"),
    [
        (None, None, ("", "")),
        ("", "", ("", "")),
        ("g", "2.0.0", ("g", "2.0.0")),
        ("g", "1.0.0", ("g", "1.0.0")),
        ("g", "3.0.0", None),
        ("g", None, None),
        ("other", "2.0.0", None),
        (None, "2.0.0", None),
    ],
)
def test_only_the_group_and_version_a_request_names_serve_it(group, version, chosen) -> None:
    router = RestRouter(
        [
            _endpoint("/users", group="", version=""),
            _endpoint("/users", group="g", version="2.0.0"),
            _endpoint("/users", group="g", version="1.0.0"),
        ]
    )

    found = router.find("GET", ["users"], group, version)

    assert (None if found is None else (found.endpoint.group, found.endpoint.version)) == chosen


def test_a_template_of_another_group_gives_way_to_a_wider_one_of_the_request_group() -> None:
    router = RestRouter([_endpoint("/users/me", group="admin"), _endpoint("/users/{id}", "GET", "POST")])

    assert _find(router, "/users/me")[0] == "/users/{id}"
    assert router.find("DELETE", ["users", "me"], None, None) == WrongMethod(frozenset({"GET", "POST"}))


@pytest.mark.parametrize(
    ("first", "second"),
    [("/users/{id}", "/users/{name}"), ("/users", "/users/"), ("/a/{p:path}", "/a/{q:path}")],
)
def test_two_endpoints_of_the_same_shape_method_and_service_conflict(first, second) -> None:
    with pytest.raises(ValueError, match="routed twice"):
        RestRouter([_endpoint(first.rstrip("/") or "/"), _endpoint(second.rstrip("/") or "/")])

    RestRouter([_endpoint(first, "GET"), _endpoint(second.rstrip("/") or "/", "POST")])
    RestRouter([_endpoint(first, group="a"), _endpoint(second.rstrip("/") or "/", group="b")])


def test_a_decoded_slash_stays_within_its_segment() -> None:
    router = RestRouter([_endpoint("/files/a/b"), _endpoint("/files/{name}")])

    found = router.find("GET", ["files", "a/b"], None, None)

    assert isinstance(found, Found)
    assert (found.endpoint.path, found.variables) == ("/files/{name}", {"name": "a/b"})


def test_endpoints_that_conflict_keep_the_first_when_not_unique() -> None:
    first, second = _endpoint("/users/{id}"), _endpoint("/users/{name}")

    router = RestRouter([first, second], unique=False)

    assert _find(router, "/users/1")[0] == "/users/{id}"
