# Quick tunnels

A quick tunnel (Cloudflare's TryCloudflare) gives a web page on the Pi a
public `https://<random-words>.trycloudflare.com` address, with no
Cloudflare account, domain or configuration. `smoking-pi tunnel` starts one
per page the edition serves, each a `cloudflared` container from a pinned
image:

| Edition | Pages |
|---|---|
| Basic | SmokePing |
| Standard | SmokePing, web admin |
| Pro | SmokePing, web admin, Grafana |

They are for a quick look from outside the house, or for showing someone
the graphs. They are not a way to run the stack.

!!! danger "Anyone with a URL reaches the page"
    The tunnel itself has no authentication. Whoever has a URL reaches that
    page, and the only thing in the way is the page's own login: the web
    admin's and Grafana's. **SmokePing's page has no login in any
    edition**, so its URL shows your graphs and target list to whoever
    holds it. The services assume a trusted network
    ([Security policy](https://github.com/estcarisimo/smoking-pi/blob/main/SECURITY.md#what-this-project-assumes-about-your-deployment)):
    share a URL only with people you would let onto that network, and stop
    the tunnels when you are done.

## Start, check, stop

```bash
sudo smoking-pi tunnel start      # asks first; --yes to skip the question
sudo smoking-pi tunnel            # status: each page and its current URL
sudo smoking-pi tunnel stop       # removes every quick tunnel
```

(`sudo` for a package install; from a clone, none.) `start` prints one line
per page, for example on Pro:

```text
  smokeping  https://brief-texts-matter-actively.trycloudflare.com
  webadmin   https://proud-dolls-buy-gently.trycloudflare.com
  grafana    https://quick-foxes-jump-highly.trycloudflare.com
```

The containers restart with Docker, so the tunnels survive a reboot until
you stop them, but each restart asks Cloudflare for a new address.
`smoking-pi tunnel` always shows the current one. A page that shows
`no URL` has not reached Cloudflare yet: wait a few seconds, or read
`docker logs` on the container it names. The stack must be up first
(`smoking-pi status`), and outbound HTTPS (port 443) must not be blocked.

## The URLs change on every start

That makes them wrong for anything that keeps a link:

- **Links in alerts and assistant answers.** `smoking-pi links --tunnel`
  accepts a quick tunnel's address, and those links work until the next
  start, then lead nowhere. For links that last, set up a named tunnel
  ([Permanent Cloudflare tunnels](cloudflare-tunnel-setup.md)) and give its
  address to `smoking-pi links --tunnel`. The two tiers of links, at home
  and from anywhere, are explained in [MCP server](mcp-server.md), *Deep
  links*.
- **Assistants.** Do not give an assistant a quick tunnel's address. Use
  `sudo smoking-pi connect --tailscale`, which publishes only the MCP
  server, behind its own sign-in, at an address that does not change
  ([Connecting any assistant](remote-connector.md)).

A named tunnel also lets you put Cloudflare Access in front of the pages,
which a quick tunnel cannot.
