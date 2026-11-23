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

"""Swagger UI and ReDoc, the pages that show the OpenAPI document, loaded from their CDNs."""

import html

from dubbo.utils.json import dump_json

__all__ = ["REDOC_CDN", "SWAGGER_UI_CDN", "redoc", "swagger_ui"]

# Major versions alone, so that fixes arrive; ReDoc 3 bundles need a module script.
SWAGGER_UI_CDN = "https://cdn.jsdelivr.net/npm/swagger-ui-dist@5"
REDOC_CDN = "https://cdn.jsdelivr.net/npm/redoc@2/bundles"


def swagger_ui(*, title: str, cdn: str, url: str, parameters: dict[str, object]) -> bytes:
    """Return the Swagger UI page of the document at ``url``, configured by ``parameters`` over its defaults."""
    configuration = {"dom_id": "#swagger-ui", **parameters, "url": url}
    # The configuration sits in a script, where `</` would end it early.
    script = dump_json(configuration).replace("</", "<\\/")
    title, cdn = html.escape(title), html.escape(cdn)
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>{title}</title>
<link rel="stylesheet" href="{cdn}/swagger-ui.css">
</head>
<body>
<div id="swagger-ui"></div>
<script src="{cdn}/swagger-ui-bundle.js"></script>
<script>window.ui = SwaggerUIBundle({script});</script>
</body>
</html>
""".encode()


def redoc(*, title: str, cdn: str, url: str) -> bytes:
    """Return the ReDoc page of the document at ``url``."""
    title, cdn = html.escape(title), html.escape(cdn)
    # The URL is the document's own, of the characters of a path, quoted as JSON quotes it.
    spec_url = dump_json(url)
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>{title}</title>
</head>
<body>
<redoc spec-url={spec_url}></redoc>
<script src="{cdn}/redoc.standalone.js"></script>
</body>
</html>
""".encode()
