# REST

A service can answer plain HTTP with JSON too, on the same port as Triple:
browsers, `curl` and services without Dubbo call it there, while Dubbo
callers keep calling over Triple. This sample serves a small todo list both
ways, from a contract in Python, and a greeter whose routes a `.proto` file
declares.

## What you will learn

- how routes map HTTP requests to the methods of a contract;
- where each parameter comes from: the path, the query or the body;
- how statuses and invalid parameters become HTTP statuses;
- how to let web pages of another origin call, with CORS;
- how to serve an OpenAPI document of the routes;
- how a `.proto` file declares routes with `google.api.http`, and
  deprecates a method.

## Run it

From this directory, start the server:

```console
$ python server.py
Serving on port 50051; press Ctrl+C to stop
```

In another terminal, call it with `curl`:

```console
$ curl localhost:50051/todos/1
{"id":1,"title":"Read the samples","done":true}
$ curl -X POST localhost:50051/todos -H 'content-type: application/json' -d '{"title":"Try curl"}'
{"id":2,"title":"Try curl","done":false}
$ curl 'localhost:50051/todos?done=false'
[{"id":2,"title":"Try curl","done":false}]
$ curl localhost:50051/todos/9
{"detail":"no todo 9"}
$ curl 'localhost:50051/todos?limit=500'
{"detail":[{"loc":["query","limit"],"msg":"Expected `int` <= 100","type":"value_invalid"}]}
```

The last two answer 404 and 422; `curl -i` shows the statuses. Then call
the same methods over Triple:

```console
$ python client.py
Added: Todo(id=3, title='Call over Triple', done=False)
All: [Todo(id=1, title='Read the samples', done=True), Todo(id=2, title='Try curl', done=False), Todo(id=3, title='Call over Triple', done=False)]
```

Open <http://localhost:50051/dubbo/openapi/swagger-ui/> in a browser to
read the routes and try them; the document itself is at
`/dubbo/openapi/openapi.json`.

### From a `.proto` file

`greeter_server.py` serves the contract generated from `greeter.proto`.
The generated `greeter_pb2.py` imports `google/api/annotations.proto`, as
any proto file it imports, so an application that serves it depends on the
package that holds it, as with gRPC; install it, then start the server:

```console
$ pip install googleapis-common-protos
$ python greeter_server.py
Serving on port 50052; press Ctrl+C to stop
```

```console
$ curl 'localhost:50052/v1/greetings/Ann?greeting=Hi'
{"message":"Hi, Ann!"}
$ curl -X POST localhost:50052/v1/greetings -H 'content-type: application/json' -d '{"name":"Bo"}'
{"message":"Hello, Bo!"}
$ curl localhost:50052/v0/hello/Cy
{"message":"Hello, Cy!"}
```

The last route belongs to a deprecated method; Swagger UI, at
<http://localhost:50052/dubbo/openapi/swagger-ui/>, shows it struck through.

## How it works

### Routes

`@dubbo.route("/todos")` on the contract prefixes the route of each method,
and `@dubbo.get`, `@dubbo.post`, `@dubbo.put`, `@dubbo.patch` and
`@dubbo.delete` give a method its route. A method without one is served on
`{prefix}/{method}`, or `/{interface}/{method}` in a contract without a
class route, unless `RestConfig(enable_default_mapping=False)` turns that
off. Only unary methods take routes.

A parameter named in the path template takes that segment. Of the others, a
scalar, such as `int`, `str`, `bool` or an enum, comes from the query, and
any other type, such as a dataclass, from the JSON body. `dubbo.Path`,
`dubbo.Query`, `dubbo.Header`, `dubbo.Cookie`, `dubbo.Body` and
`dubbo.Form` in an `Annotated` type choose the place themselves, and take
constraints such as `ge` and `max_length`, which calls over plain HTTP
check.

### Statuses

A method returns 200 unless its route sets `status_code`, as `add` does
with 201. An `RpcError` becomes the HTTP status of its gRPC status, as
`NOT_FOUND` becomes 404, with the message in `detail`; an invalid
parameter answers 422 and says which.

### Serialization

Plain HTTP always carries JSON. `ProviderConfig(serialization="json")`
makes Triple carry the same JSON, so that the dataclasses of the contract
travel alike both ways; the client sets it too, as it calls by URL rather
than through a registry, which would tell it.

`serialization="msgpack"` on both sides carries MessagePack instead, more
compact where no web client reads the Triple messages; plain HTTP still
carries JSON. Both formats are written by msgspec, and are Triple's alone:
the Dubbo protocol carries Hessian 2, which Java reads.

### CORS and OpenAPI

Plain HTTP is off unless `RestConfig(enabled=True)` turns it on, in the
`TripleConfig` of the protocol. `CorsConfig(allowed_origins=...)` answers
the preflight requests of browsers for the pages of those origins:

```console
$ curl -i -X OPTIONS localhost:50051/todos/1 -H 'origin: https://app.example' -H 'access-control-request-method: GET'
HTTP/1.1 204
access-control-allow-origin: https://app.example
access-control-allow-methods: GET, HEAD, POST
access-control-max-age: 1800
vary: origin
```

`OpenAPIConfig(enabled=True)` serves the OpenAPI 3.1 document of the
routes, with Swagger UI and ReDoc to read it. The `summary` of `find` and
the constraint on `limit` appear there.

### Routes in a `.proto` file

A method of a `.proto` file takes its routes from the `google.api.http`
option, as gRPC transcoding does, so that one IDL describes both:

```proto
rpc SayHello(HelloRequest) returns (HelloReply) {
  option (google.api.http) = {
    get: "/v1/greetings/{name}"
    additional_bindings {
      post: "/v1/greetings"
      body: "*"
    }
  };
}
```

`{name}` takes that field of the request from the path; the others come
from the query, unless `body` names a field, or `*` for the whole message,
to come from the JSON body. `response_body` would answer with one field of
the reply instead of all of it. The messages travel in their proto3 JSON
mapping, as gRPC gateways send them.

`protoc-gen-python-dubbo` turns the option into `@dubbo.get` and
`@dubbo.post` on the generated contract, in `greeter_dubbo.py`; serving
reads the routes from there, not from the proto file. To generate it
again, install the `protoc` extra, which brings `google/api` too, and give
protoc the directory that holds it:

```console
$ pip install "apache-dubbo[protoc]"
$ python -m grpc_tools.protoc -I. -I"$(python -c 'import google.api.annotations_pb2 as m, pathlib; print(pathlib.Path(m.__file__).parents[2])')" --python_out=. --pyi_out=. --python-dubbo_out=. greeter.proto
```

A path takes literal segments and variables of one segment, `{field}` or
`{field.inner}`; a pattern such as `{name=shelves/*}` or a `:verb` suffix
fails the generation. Streaming methods take no routes.

`option deprecated = true` on `Hello` marks the generated method with
`typing_extensions.deprecated` (PEP 702), so that a type checker reports each call
of it, and the OpenAPI document marks its route `deprecated`. The method
is still served. To warn the consumers of a running provider too, its
configuration sets `deprecated: true` on the method, as in
`ServiceConfig(methods={"Hello": MethodConfig(deprecated=True)})`, and
each consumer logs a warning the first time it calls it.

## Next

- [19_http3](../19_http3): calls over HTTP/3, and the way back to HTTP/2.
