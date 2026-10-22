# watch_rules.md

Watching the wire between the trader and the paper broker, plus the nginx
block that serves the report directory. Every line carries its reason.
Replace `203.0.113.10` (TEST-NET-3) with the broker's real address and
`reports.nohindsight.internal` with the real hostname.

## Wireshark display filters

First find the broker host if you only know the name. Do this once, then
pin the IP for the filters below.

```
dns.qry.name contains "broker"
```

Isolate all traffic to and from the broker API host. `ip.addr` matches both
directions, so requests and replies stay in one view.

```
ip.addr == 203.0.113.10
```

Long-lived or sick TCP sessions. `tcp.analysis.flags` is the umbrella for
every Wireshark TCP diagnosis: retransmissions, duplicate ACKs, zero window,
out of order. A clean request-response API session produces none of these,
so any hit means a stall or a lossy path. `tcp.time_delta > 5` catches the
other failure mode: five seconds of silence since the previous segment on
the same stream, which on an order-placement call means a hang.

```
tcp.analysis.flags || tcp.time_delta > 5
```

Plaintext API keys in the clear. `http.authorization` matches any HTTP
request carrying an Authorization header without TLS, and the `contains`
form catches a custom `api-key` header. The broker must be HTTPS-only, so
a single hit means a key crossed the wire in plaintext and needs rotation
before anything else happens.

```
http.authorization || http contains "api-key"
```

Order traffic only, when the broker mixes market data and orders on the
same host. Keeps the manual review queue short.

```
http.request.method == "POST" && http.request.uri contains "orders"
```

## Capture filter (BPF) variant for port 443 SNI

BPF runs in the kernel before any reassembly, so it can match ports and
hosts but cannot parse TLS. Capture narrowly, then extract the SNI after
the fact. Narrow capture matters on a busy host: it keeps the ring buffer
from churning.

```
tcp port 443 and host 203.0.113.10
```

SNI extraction from the capture. The SNI lives in the ClientHello's
extensions, and `tshark` pulls it per flow:

```sh
tshark -r broker.pcap -Y "tls.handshake.type == 1" \
    -T fields -e tls.handshake.extensions_server_name
```

Same check live, no capture file, for when you only want to confirm the
trader is talking to the broker and not to a lookalike domain:

```sh
tshark -i eth0 -f "tcp port 443" \
    -Y "tls.handshake.extensions_server_name == \"paper-broker.example\""
```

## nginx server block for the report directory

```nginx
# Rate limit zone. 10MB of shared state tracks roughly 160k addresses.
# 10 requests per second per IP is generous for a human reading reports
# and still bottlenecks a directory enumeration sweep.
limit_req_zone $binary_remote_addr zone=report_rl:10m rate=10r/s;

server {
    listen 80;
    server_name reports.nohindsight.internal;

    # The gallery output tree. Reports are static files, no backend.
    root /srv/nohindsight/gallery;

    # Never list the directory. The report filenames are unguessable
    # hashes, and autoindex would hand an attacker the full set.
    autoindex off;

    # Apply the limiter with a burst of 20 queued requests. nodelay keeps
    # the burst instant instead of metering it out, which is what a human
    # with a dozen open tabs expects.
    limit_req zone=report_rl burst=20 nodelay;

    # Don't let the browser sniff content types. Reports are text and
    # images served with correct types; sniffing turns a stray .html
    # upload into script execution.
    add_header X-Content-Type-Options "nosniff" always;

    # Reports load nothing off-site. default-src 'self' blocks any
    # injected remote script or tracker from inside a report page.
    add_header Content-Security-Policy "default-src 'self'" always;

    # 404 instead of serving a wrong file. No rewrites, no fallbacks,
    # nothing dynamic to exploit.
    location / {
        try_files $uri =404;
    }

    # TLS: uncomment after `certbot --nginx -d reports.nohindsight.internal`
    # has run once. certbot writes and maintains these lines itself, so the
    # commented copy here is only the shape to expect. A broker report names
    # positions and PnL, and it should not cross the wire in plaintext.
    #
    # listen 443 ssl http2;
    # ssl_certificate     /etc/letsencrypt/live/reports.nohindsight.internal/fullchain.pem;
    # ssl_certificate_key /etc/letsencrypt/live/reports.nohindsight.internal/privkey.pem;
    # include             /etc/letsencrypt/options-ssl-nginx.conf;
}
```
