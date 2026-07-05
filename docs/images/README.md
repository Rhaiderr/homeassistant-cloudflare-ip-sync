# Screenshots

Screenshots referenced by the main README (as HTML comments, so nothing renders broken until
they exist). To enable one, capture it, save it here with the matching filename, and replace
the corresponding `<!-- screenshot: ... -->` comment in the README with a real image tag:

| File | What to capture |
| --- | --- |
| `create-list.png` | Cloudflare → Manage Account → Configurations → Lists → Create new list (name + content type *IP addresses*) |
| `waf-rule.png` | Zone → Security → WAF → Custom rules — the `not ip.src in $casa` expression and Block action |
| `create-token.png` | My Profile → API Tokens → Create Custom Token — showing the two permissions and Account Resources |

**Redact before committing**: account IDs, e-mail addresses, zone names and any token values
visible on screen.
