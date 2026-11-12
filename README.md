# CMS Reporter

Monthly royalty settlement and client reporting for networks that manage content
through YouTube CMS: multi-channel networks, music labels and other rights holders.

Every month a network downloads revenue reports from CMS, works out which asset
belongs to which client, deducts tax and its own commission, and sends each client
a statement. CMS Reporter turns that into four guided steps:

1. **Add files.** Drop in the CMS exports as downloaded.
2. **Match assets.** Assets nobody has labelled are listed with their revenue, so
   they can be assigned instead of silently dropping out. Matches are remembered.
3. **Review.** What each client earned, what is withheld, what the network keeps,
   and what is paid, in USD and local currency.
4. **Send.** A PDF report with charts for each client, plus the underlying revenue
   rows as Excel and CSV. Everything is password protected.

It also keeps dated **snapshots of every channel's metadata** (the evidence YouTube
asks for if a channel is hijacked) and pushes **copyright and licence conflicts** to
Telegram, WhatsApp or e-mail so deadlines are not missed.

> **All data in this repository is synthetic.** The producers, channels, assets and
> amounts in `config/network.yaml` and in the demo are generated and fictional.

## Try it

**Live demo:** _add your Render URL here once deployed._ No installation; each
visitor gets a private workspace with six months of made-up data and can upload
their own CMS exports, which are deleted after two hours.

**On your own machine** (needs Docker, nothing else):

```bash
cp .env.example .env
docker compose build
docker compose run --rm cli keygen        # paste the output into .env as CMS_MASTER_KEY
docker compose run --rm cli demo          # six months of synthetic data
docker compose up app                     # open http://localhost:8000
```

## How it is built

| Layer | Technology |
|---|---|
| Web interface | React, TypeScript, Vite. Charts are hand-written SVG. |
| API | FastAPI |
| Settlement and reports | Python: pandas, ReportLab, matplotlib |
| Encryption | Office and PDF AES encryption, AES-256 zip, Fernet, keys derived by HMAC |
| Packaging | One Docker image (multi-stage build), Docker Compose |
| Tests | pytest: settlement arithmetic, encryption, the API, visitor isolation |

The settlement logic is a plain Python package with a command line, so everything
the web interface does can also be scripted:

```bash
docker compose run --rm cli run 2026-11 --no-write        # settlement only, on screen
docker compose run --rm cli unidentified 2026-11
docker compose run --rm cli assign music A123456789012345 "Saffron Tapes"
docker compose run --rm cli run 2026-11                   # write encrypted outputs
docker compose run --rm cli deliver 2026-11               # dry run; add --send to e-mail
docker compose run --rm cli passwords
```

## How the settlement works

A revenue row is attributed to a client by the first rule that matches:

1. a manual assignment for its Asset ID
2. one of its Asset Labels
3. a pattern found in its Custom ID (compared in lowercase, ignoring spaces)
4. for per-channel Shorts reports, its Channel ID

Then, for each client in each vertical (Music, Non-Music):

```
net        = revenue + Shorts share + other earnings - withholding tax
commission = net x the client's commission rate
payment    = net - commission
```

Withholding tax is a percentage of the revenue earned in a country (10% of US
revenue in the sample config). A client can appear in several verticals with a
different rate in each; the statement adds them up.

Rows with zero or negative revenue are ignored, so negative adjustments are not
netted against a client. Change `load_vertical` in `ingest.py` if your contracts
say otherwise.

### Month folder

The web interface manages this for you; it is what the command line reads.

```
data/inbox/2026-11/
  period.yaml            exchange rate or payout, Shorts pool, other earnings
  music/*.csv            CMS exports for the Music vertical
  non_music/*.csv        CMS exports for the Non-Music vertical
```

```yaml
# period.yaml: figures that are not in the CMS exports
period: 2026-11
fx:
  currency: TRY
  payout_local: 4250000.00   # amount received; the effective rate is derived from it
  # rate: 44.59              # or state the rate directly
shorts_pool_usd:
  music: 512.40              # split between clients in proportion to their revenue
other_earnings:
  - producer: Blue Heron Records
    vertical: music
    amount_usd: 19.37
    note: Channel store (e-commerce)
```

## Configuration

**`config/network.yaml`** holds the network's clients: commission rates, Custom ID
patterns, asset labels, channels and contact addresses, per vertical. Matching
assets in the web interface adds rules to this file. The one in this repository is
a fictional example; replace it with your own and keep the real one out of version
control.

**`.env`** holds secrets and integration settings. See `.env.example` for the full list.

| Setting | Needed for |
|---|---|
| `CMS_MASTER_KEY` | Everything. Required. |
| `YOUTUBE_API_KEY` | Channel snapshots of real channels |
| `YT_CLIENT_SECRET_FILE`, `YT_CONTENT_OWNER_ID` | Audience charts from the YouTube Analytics API |
| `SMTP_*` | E-mailing client packages and e-mail alerts |
| `TELEGRAM_*`, `TWILIO_*`, `WHATSAPP_TO`, `ALERT_EMAIL_TO` | Conflict alerts |
| `IMAP_*` | Reading YouTube's notification e-mails as a conflict source |
| `CMS_DEMO_MODE` | Public demo instances only |

The web interface has no login of its own. Compose binds it to `localhost`; put it
behind your own authentication before exposing it on a network.

## Encryption

One master key protects everything, and nothing financial is written to disk in the
clear.

- Each client has a password derived from the master key. It stays the same every
  month, so it only has to be shared once. Share it through a different channel than
  the files.
- Excel workbooks use Office password protection, CSVs are in an AES-256 zip, and
  PDFs use AES-256 PDF encryption. All of them open with ordinary desktop software.
- The internal settlement workbook has its own password. The month-to-month history
  used for trend charts is stored encrypted.
- The on-screen report preview is rendered on request and never saved.

Keep the master key safe and backed up. If it is lost, existing files cannot be
opened. If it is changed, every password changes.

## Conflict alerts on Telegram and WhatsApp

New ownership conflicts, disputes and takedowns are announced once on every channel
that is configured in `.env`. Conflicts are read from issue exports placed in
`data/conflicts/` and, when `IMAP_*` is set, from YouTube's notification e-mails.

```bash
docker compose run --rm cli conflicts --demo       # try it with a made-up export
docker compose --profile alerts up -d              # check every 15 minutes
```

The **Alerts** page shows the message as it arrives in a chat and has a
**Send test alert** button that delivers a sample alert to a real phone:

- **Telegram:** create a bot with [@BotFather](https://t.me/BotFather) and set
  `TELEGRAM_BOT_TOKEN` (and `TELEGRAM_BOT_USERNAME` so the page can link to it).
  The recipient presses Start in the bot and enters their chat ID.
- **WhatsApp:** set `TWILIO_ACCOUNT_SID`, `TWILIO_AUTH_TOKEN` and
  `TWILIO_WHATSAPP_FROM`. With Twilio's sandbox, also set `TWILIO_SANDBOX_JOIN` to
  the join phrase; recipients send it to the sandbox number once.

This also works on the public demo. It is the only thing the demo sends, and it is
limited: the text is fixed, each visitor gets three sends, and the destination is
not stored. Without the settings above, the page shows the preview only.

## Hosting the demo

`render.yaml` deploys the public demo to [Render](https://render.com)'s free plan:

[![Deploy to Render](https://render.com/images/deploy-to-render-button.svg)](https://render.com/deploy?repo=https://github.com/HikmetEmre/YouTube_CMS_Reporter)

Press the button (or, in Render, **New > Blueprint** and choose the repository), then **Apply**.

Render builds the Dockerfile, generates a master key and sets `CMS_DEMO_MODE=1`.
The free plan sleeps after a period without visitors, so the first request
afterwards takes up to a minute.

In demo mode every visitor gets a private copy of a synthetic workspace, tied to a
browser cookie and deleted after two hours. Uploads are limited to 25 MB per file
and 60 MB per visitor, e-mail and chat alerts are disabled (apart from the optional test
alert above), and channel snapshots use made-up channels. Any host that runs a Docker image works the same way: set the
two environment variables and route traffic to `$PORT` (default 8000).

## What has not been tested against live services

The settlement, encryption, reports, API and web interface are covered by tests and
run on synthetic data. The integrations below are implemented but could not be
exercised without real accounts, so expect to adjust them on first use:

- YouTube Analytics API (audience charts)
- YouTube Data API with a real key (the demo uses a stand-in with the same response shape)
- Telegram, Twilio WhatsApp, SMTP and IMAP
- Real CMS issue exports: accepted column names are listed in `CSV_FIELDS` in `conflicts.py`

## Development

```bash
python -m venv .venv && . .venv/bin/activate     # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
export CMS_MASTER_KEY=$(cms-reporter keygen)

pytest                                   # all tests
pytest tests/test_core.py -k settlement  # a single test
ruff check . && ruff format .

cd web && npm install && npm run build   # builds the interface into the Python package
cms-reporter demo                        # synthetic data into ./data
cms-reporter serve                       # http://localhost:8000
```

While working on the interface, run `cms-reporter serve` and, in `web/`, `npm run dev`
for hot reload; API calls are proxied to port 8000.

```
src/cms_reporter/
  config.py       network config and paths
  ingest.py       read CMS exports
  mapping.py      attribute rows to clients
  settlement.py   tax, commission, payment, FX
  pipeline.py     the monthly run and its outputs
  security.py     keys, passwords, encrypted files
  store.py        manual assignments, encrypted history
  charts.py       PDF report charts
  report_pdf.py   the client PDF
  analytics.py    audience data (demo and YouTube Analytics)
  backup.py       channel snapshots
  conflicts.py    conflict sources and notifiers
  delivery.py     e-mail client packages
  synth.py        synthetic CMS exports
  workspace.py    per-visitor workspaces (demo mode), config edits
  api.py          HTTP API, serves the web interface
  cli.py          command line
web/src/          React interface (Month.tsx is the guided month)
```

## Licence

[MIT](LICENSE)
