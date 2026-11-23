# HTTP/3

Triple runs over HTTP/3 too, on QUIC over UDP: no head-of-line blocking
between calls when packets are lost, and connections that survive a change
of address. A caller starts on HTTP/2, learns from the server that HTTP/3
is there, and moves to it, falling back to HTTP/2 whenever HTTP/3 is not
ready. This sample shows each step in the log.

## What you will learn

- how a server serves HTTP/3 beside HTTP/2;
- how a caller finds HTTP/3 and moves its calls there;
- what happens when HTTP/3 is idle, lost or blocked.

## Before you start

HTTP/3 needs the `http3` extra, and runs over TLS alone; the sample uses
the certificates of [16_tls](../16_tls), made with `trustme`:

```console
$ pip install "apache-dubbo[http3]" trustme
$ python ../16_tls/make_certificates.py
```

## Run it

From this directory, start the server:

```console
$ python server.py
Serving HTTP/2 and HTTP/3 on port 50051; press Ctrl+C to stop
```

In another terminal, run the client:

```console
$ python client.py
INFO dubbo.application: Started the application
Hello, Alice!
INFO dubbo.protocol.triple.connection: Calls to localhost:50051 move to HTTP/3
Hello, Bob!
Hello, Carol!
DEBUG dubbo.protocol.triple.connection: Calls to localhost:50051 move to HTTP/3
Hello, Dave!
INFO dubbo.application: Stopped the application
```

## How it works

The contract is written in Python, so both sides set `serialization="json"`
for Triple to carry its values as JSON; [18_rest](../18_rest) explains it.

### Finding HTTP/3

`Http3Config(enabled=True)` in the `TripleConfig` of the protocol has the
server serve HTTP/3 on the UDP port of the same number as its TCP port, and
announce it in an `alt-svc` field of its HTTP/2 responses, RFC 7838, as web
servers announce HTTP/3 to browsers.

On the client, `Http3Config(enabled=True)` in the `TripleConfig` of the
consumer, or of one reference, lets calls use it. The first call goes over
HTTP/2; its response announces HTTP/3, which connects in the background,
and the calls after it go over HTTP/3: that is the INFO line before Bob.
`Http3Config(negotiation=False)` skips HTTP/2 and calls over HTTP/3 alone,
for a server known to serve it.

### When HTTP/3 is not ready

A call never waits for a QUIC handshake while HTTP/2 can take it:

- **Idle**: a connection without traffic closes after the idle timeout,
  30 seconds by default, 1 second here. The next call, Carol's, goes over
  HTTP/2, while HTTP/3 connects again in the background for the calls
  after it, Dave's. This is routine, so it logs at DEBUG only.
- **Lost**: a call whose HTTP/3 connection fails before its request goes
  out moves to HTTP/2; one already sent fails, as it may have reached the
  server.
- **Blocked**: where a firewall drops UDP, the handshake fails, and the
  client leaves HTTP/3 for 5 minutes, then twice as long after each failure
  in a row, up to two days, as browsers do; a success forgets the failures.
  The log says so:

  ```console
  WARNING dubbo.protocol.triple.connection: Calls to localhost:50051 go back to HTTP/2 for 300 seconds: failed to connect to localhost:50051
  ```

A call stays on the version its stream was opened on.

## Next

- [interop/java](../../interop/java): call Java services from Python, and
  Python services from Java.
