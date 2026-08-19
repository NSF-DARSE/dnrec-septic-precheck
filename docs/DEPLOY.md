# Deploying the reviewer console

**Status: the image definition has not been built.** Docker is not installed on the
development machine, so the `Dockerfile` here is written and path checked but never
compiled. Budget time for a first build to surface something, and build it before
you need it rather than on the day.

## Why this deploys without credentials

The console reads cached Textract output from disk, the GIS layers from
`data/gis`, and the rules from `rules_7101.yaml`. A running container needs no IAM
role, no access key and nothing from the environment.

That is the property that matters for a workshop account: **credentials are needed
to create the deployment, never to serve it.** A container started at 11am keeps
serving after the keys expire in the afternoon. The one path that would need
Textract is uploading a brand new PDF, and that fails with a clear message rather
than hanging.

Ask the organisers whether the account is **torn down** at the end rather than the
keys merely expiring. If the account gets deleted, the URL dies with it, and it
should not go on a slide or in the README.

## Build

```bash
python scripts/build_image_context.py     # stages 20.8 MB into docker-context/
docker build -t septic-precheck .
docker run --rm -p 8501:8501 septic-precheck
```

Then open http://localhost:8501.

`build_image_context.py` exists because the Textract cache is 1.1 GB across 243
documents. The console only ever reads the four demo packets and the cache entries
keyed by their SHA256, which is 20.8 MB. Rerun it whenever a demo packet changes,
because the cache key is the hash of the file's bytes.

It exits non-zero and names any packet with no cached analysis. Do not build past
that: a packet with no cache asks for credentials at review time, which is exactly
what the container cannot do.

## Where to run it

Streamlit holds a **WebSocket** open. That constrains the options more than
anything else about this app.

**EC2, one t3.small, with a reverse proxy for TLS.** Fastest real path, full
control, about $15 a month. This is the one to pick if a URL is needed this week.

**ECS Fargate behind an ALB.** The ALB handles WebSockets and sticky sessions
properly. More setup, but it is the version that looks like production if someone
asks how it would run. Roughly $20 to $30 a month idle.

**App Runner.** Tempting, because it is container to URL with no infrastructure.
Verify WebSocket support before spending time on it. If it does not proxy
WebSockets, Streamlit will not work at all.

**Amplify, or S3 with CloudFront.** No. Those serve static frontends and this is a
Python server.

## Before making the URL public

The packets carry property addresses. It is public record data from
data.delaware.gov, but a public unauthenticated URL still republishes it. Put it
behind basic auth or a login rather than leaving it open.

## Order of work on demo day

Deploy **early**, not at the end. Nothing is gained by waiting and the failure mode
of a late deploy is losing the URL entirely.

Present from **localhost**, with the URL shown as evidence it deploys rather than
as the thing being driven. Never demo live off a URL that started working three
hours earlier. `docs/DEMO.md` is the runbook for the demo itself.
