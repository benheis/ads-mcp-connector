# ads-mcp-connector

Connect Claude Code to Meta Ads and Google Ads. Ask questions about your campaigns in plain English. No CSV exports, no copy-pasting, no manual data pulls.

Built for marketing teams by [@benheiser](https://linkedin.com/in/benheiser).

---

## Installation

Paste this into your Terminal and press Enter:

```bash
curl -fsSL https://raw.githubusercontent.com/bennymayo/ads-mcp-connector/main/install.sh | bash
```

> **What is a terminal?** On a Mac, press `Cmd + Space`, type "Terminal", and press Enter. On Windows, search for "Command Prompt". It's just a text-based way to talk to your computer — think of this command as downloading and installing the connector automatically.

**Requirements:**
- [Claude Code](https://claude.ai/code) installed
- Python 3.10 or newer ([download here](https://www.python.org/downloads/) — takes 3 min)
- A Meta Ads and/or Google Ads account

The installer will:
- Download this connector to `~/ads-mcp-connector`
- Install Python dependencies automatically
- Register itself with Claude Code
- Install a security hook that scans for API keys before every git commit

---

## Usage

Open Claude Code, navigate to any project, and type:

```
/ads-connect
```

The skill walks you through connecting your ad accounts step by step. Once connected, ask questions in plain English:

```
Show me my campaigns from last month

What search terms are triggering my Google Ads?

Compare Meta and Google spend this week

Break down my top campaign by age and gender

What's my ROAS across both platforms?
```

---

## What gets connected

### Meta Ads
- Campaign performance (spend, impressions, clicks, CTR, CPC)
- Ad set breakdowns (by age, gender, placement, device)
- Individual ad performance with creative details
- Account-level overview

### Google Ads
- Campaign performance (cost, clicks, conversions, ROAS)
- Keyword performance with Quality Scores
- Search term report — see exactly what searches triggered your ads
- Ad group breakdown

---

## How credentials are stored

Your API keys are stored in a `.env` file at `~/ads-mcp-connector/.env`. This file is:
- **Gitignored** — never committed to GitHub
- **Local only** — stays on your machine
- **Written by the skill** — you never need to edit it manually

The `/ads-connect` skill writes your credentials directly from Claude chat to the `.env` file. You paste a token in chat, the skill saves it securely — the full value is never echoed back.

### Credential lifespans

| Platform | Method | Expires |
|----------|--------|---------|
| Meta Ads | System User token (recommended) | Never |
| Meta Ads | Graph API Explorer token (fallback) | 60 days |
| Google Ads | OAuth2 refresh token | Never (unless revoked) |

---

## Security

Every commit to this repo is scanned automatically for exposed credentials.

If you accidentally paste an API key into a source file and try to commit it, the commit will be blocked with a plain-English explanation of what to do.

To run a manual scan:
```bash
python secrets_check.py
```

---

## Connecting to other tools

The MCP server exposes these tools to Claude Code. You can reference them directly in any Claude session:

| Tool | What it does |
|------|-------------|
| `check_connection` | Test credentials for both platforms |
| `meta_get_campaigns` | List campaigns with performance data |
| `meta_get_ad_sets` | Ad sets with targeting and delivery |
| `meta_get_ads` | Individual ads with creative and metrics |
| `meta_get_account_overview` | Top-level account stats |
| `meta_get_insights` | Breakdown by age, gender, placement, device |
| `google_get_campaigns` | Campaigns with cost, conversions, ROAS |
| `google_get_keywords` | Keywords with Quality Score |
| `google_get_search_terms` | Actual searches triggering your ads |
| `google_get_account_overview` | Account-level ROAS and impression share |
| `google_get_ad_groups` | Ad group performance |

---

## Troubleshooting

**Claude Code doesn't recognize `/ads-connect`**
Run `bash ~/ads-mcp-connector/install.sh` again and restart Claude Code.

**Meta token expired**
Run `/ads-connect` — the skill detects expiration automatically and walks you through renewal (2 min). Avoid this entirely by using the System User setup path (token never expires).

**Google returns an auth error**
Your refresh token may have been revoked. Go to [myaccount.google.com/permissions](https://myaccount.google.com/permissions), remove access for your app, then run `python get_google_token.py` to generate a new one.

**Pre-commit hook not running**
Run `bash install.sh` from inside the repo directory to reinstall it.

---

## Related repos

- [Curtis Howland's Facebook ad diagnostic framework](https://linkedin.com/in/curtishowland/) — the companion skill that runs automated ad performance diagnostics using this connector

---

## License

MIT — use it, fork it, build on it.
