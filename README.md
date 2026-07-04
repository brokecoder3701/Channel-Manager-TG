# Mamba's Insider auto-poster

Posts AI-rewritten updates to https://t.me/mambasinsider every 15 minutes, covering:
- Crypto / Trump / Elon / Fed news (filtered RSS feeds)
- Polymarket odds swings on tracked topics
- Performance updates on tokens you've "called"

Runs for free on GitHub Actions — no server needed.

## 1. Create the Telegram bot

1. Message **@BotFather** on Telegram, send `/newbot`, follow the prompts.
2. Copy the bot token it gives you (looks like `123456:ABC-def...`).
3. Go to your channel (**@mambasinsider**) → Administrators → Add Admin → add your new bot → give it permission to **Post Messages**.

## 2. Get an Anthropic API key

Go to https://console.anthropic.com, create an API key. This is what rewrites raw headlines into punchy posts. Cost is very low — this uses Claude Haiku and only a few dozen short calls per day.

## 3. Put this code in a GitHub repo

1. Create a new **private** GitHub repo (private so your config/keys structure isn't public).
2. Upload all these files (or `git push` them) keeping the folder structure, especially `.github/workflows/post-updates.yml`.

## 4. Add your secrets

In the repo: **Settings → Secrets and variables → Actions → New repository secret**. Add:

| Name | Value |
|---|---|
| `TELEGRAM_BOT_TOKEN` | the token from BotFather |
| `ANTHROPIC_API_KEY` | your Anthropic API key |

## 5. Turn it on

The workflow runs automatically every 15 minutes once it's on the default branch. You can also trigger it manually: **Actions tab → Post updates to Mamba's Insider → Run workflow**, which is the fastest way to test it.

## 6. "Calling" a token — fully automatic now

Just post the Solana contract address (CA) in the channel, like:

```
9sKaFxZbC4aHEb9PLmiJ9Hdf5Ad745NQkgu3PHEupump
```

On the next run (within 15 min), the bot will:
1. Scan new channel posts for anything that looks like a Solana address
2. Check it against DexScreener — if it's a real, liquid token, it starts tracking it automatically at the current price
3. Reply in the channel confirming the call ("📌 Just aped $XYZ...")
4. From then on, auto-posts updates whenever it's up 25/50/100/200/500% or down 25/50/75% from that entry price, once per threshold
5. Automatically stops posting on a token once it's down 90%+ (marks it inactive so it doesn't spam a dead coin forever)

No file editing needed — this only works for **Solana** tokens (matches pump.fun and any other Solana CA). You can post the CA as a standalone message or inside a longer caption, either works.

**Note:** since the bot polls on a schedule rather than reacting instantly, there's up to a ~15 min lag between you posting the CA and the bot's confirmation reply — that's the tradeoff for running this for free on GitHub Actions instead of a paid always-on server.

## Tuning

Edit `config.json`:
- `news_feeds` — add/remove RSS sources
- `news_keywords` — what counts as relevant
- `polymarket_search_terms` — topics to watch for odds swings
- `polymarket_swing_threshold_pct` — how big a move triggers a post
- `max_news_posts_per_run` / `max_polymarket_posts_per_run` — flood control

## Known limitations

- GitHub Actions is schedule-based, not instant — expect up to a 15 min delay, and free-tier scheduled workflows can occasionally run a bit late during high load.
- Free public news RSS feeds can be noisy; expect to tune `news_keywords` after the first day of real posts.
- CoinGecko's free API has rate limits — fine for a handful of tracked calls, but don't add dozens at once.
