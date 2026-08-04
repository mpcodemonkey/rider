# jockiefluxer

A self-hosted **drop-in replacement for [Jockie Music](https://www.jockiemusic.com/)**, built for
[Fluxer](https://fluxer.app).

Same `m!` prefix, same command names, same aliases. Point it at your own Fluxer instance and your
group keeps typing exactly what it typed before — `m!play`, `m!skip`, `m!q`, `m!247`, `m!loop`.

Built for long tabletop sessions: saved playlists, 24/7 mode, gapless looping and per-track
seeking, all running on hardware you control.

---

## Why this isn't just discord.py with the URL changed

Fluxer's HTTP and gateway APIs are largely wire-compatible with Discord's, but **voice is not**.
Discord bots stream Opus over their own UDP protocol; Fluxer runs voice through **LiveKit**
(WebRTC). A Discord music bot's entire audio layer therefore does not transfer.

This bot uses [`fluxer.py`](https://github.com/Fluxer-py/fluxer.py) for the gateway and REST, and
drives the audio itself: it publishes **one** LiveKit audio track per voice session and pushes
20 ms PCM frames into it, swapping the ffmpeg process underneath when the track changes. That is
what makes transitions gapless, `seek` accurate, and `volume` instant.

```
yt-dlp ──► stream URL ──► ffmpeg (s16le 48kHz stereo, filters baked in)
                                   │
                            20 ms frames + gain
                                   │
                          livekit.rtc.AudioSource ──► Fluxer voice channel
```

---

## Requirements

- Python 3.10+
- **ffmpeg** on `PATH`
- A bot account on your Fluxer instance

## Install

```bash
git clone <this repo> && cd jockiefluxer
pip install .

cp .env.example .env
$EDITOR .env          # set FLUXER_TOKEN (and FLUXER_API_URL if self-hosting)

python -m jockiefluxer
```

### Docker

```bash
cp .env.example .env && $EDITOR .env
docker compose up -d
```

The `data/` volume holds saved playlists, favourites and per-server settings — back that up and
you keep everything across upgrades.

### Pointing at a self-hosted Fluxer

Set `FLUXER_API_URL` to your instance's API base, e.g.
`FLUXER_API_URL=https://fluxer.example.com/api/v1`. The gateway URL is discovered from
`/gateway/bot`, so nothing else needs configuring.

### Bot permissions

Invite the bot with **View Channel**, **Send Messages**, **Embed Links**, **Read Message History**,
**Connect** and **Speak**. Enable the **Message Content** intent — prefix commands cannot work
without it.

---

## Commands

Every command below also works with an @mention instead of the prefix, and the prefix is
case-insensitive (`m!play` and `M!play` are the same). Change it per server with `m!prefix`.

### Music

| Command | Aliases | Description |
| --- | --- | --- |
| `m!connect` | `join`, `summon` | Bring the bot into your voice channel. |
| `m!disconnect` | `dc`, `leave`, `bye` | Leave the voice channel and clear the queue. *(DJ)* |
| `m!forceskip [amount]` | `fs` | Skip immediately, no vote. *(DJ)* |
| `m!forward [time]` | `fwd`, `ff` | Skip ahead (default 10 seconds). *(DJ)* |
| `m!grab` | `save`, `bookmark` | Send the current track to your DMs. |
| `m!nowplaying` | `np`, `playing`, `current`, `song` | Show what's playing right now. |
| `m!pause` | — | Pause playback. *(DJ)* |
| `m!play <song or URL>` | `p`, `add` | Play a track, or add it to the queue. |
| `m!playnext <song or URL>` | `pn`, `playtop`, `ptop` | Add a track to the front of the queue. *(DJ)* |
| `m!playnow <song or URL>` | `pnow` | Play a track immediately, pushing the current one back. *(DJ)* |
| `m!previous` | `prev`, `back` | Play the previous track again. *(DJ)* |
| `m!replay` | `restart` | Restart the current track from the beginning. *(DJ)* |
| `m!resume` | `unpause`, `continue` | Resume playback. *(DJ)* |
| `m!rewind [time]` | `rwd`, `backward` | Skip backwards (default 10 seconds). *(DJ)* |
| `m!search <query>` | `find` | Search and pick a result to queue. |
| `m!seek <time>` | — | Jump to a timestamp, e.g. 1:30 or 90s. *(DJ)* |
| `m!skip [amount]` | `s`, `next`, `voteskip` | Skip the current track (votes if you're not a DJ). |
| `m!skipto <position>` | `jump`, `jumpto` | Jump straight to a queue position. *(DJ)* |
| `m!stop` | — | Stop playback and clear the queue. *(DJ)* |
| `m!volume [0-200]` | `vol`, `v` | Show or set the playback volume. |

### Queue

| Command | Aliases | Description |
| --- | --- | --- |
| `m!autoplay [on\|off]` | `ap` | Keep playing related tracks when the queue runs out. *(DJ)* |
| `m!clear` | `clearqueue`, `cq`, `empty` | Remove everything from the queue. *(DJ)* |
| `m!history [page]` | `recent`, `played` | Show recently played tracks. |
| `m!loop [off\|track\|queue]` | `repeat`, `l` | Loop the current track (or toggle it off). *(DJ)* |
| `m!loopqueue` | `lq`, `repeatqueue`, `rq` | Loop the whole queue. *(DJ)* |
| `m!move <from> <to>` | `mv` | Move a queued track to another position. *(DJ)* |
| `m!queue [page]` | `q`, `list`, `songs` | Show the queue. |
| `m!remove <position \| range>` | `rm`, `delete`, `del` | Remove queue entries, e.g. 3, 2-5 or 1,4,7. *(DJ)* |
| `m!removedupes` | `dedupe`, `distinct` | Remove duplicate tracks from the queue. *(DJ)* |
| `m!removeuser <@user>` | `removesongs` | Remove everything a specific user queued. *(DJ)* |
| `m!shuffle` | `mix` | Shuffle the queue. *(DJ)* |
| `m!swap <first> <second>` | — | Swap two queued tracks. *(DJ)* |

### Filters

| Command | Aliases | Description |
| --- | --- | --- |
| `m!8d` | `eightd`, `rotation` | Rotating stereo effect. *(DJ)* |
| `m!bassboost [off\|low\|medium\|high\|extreme]` | `bb`, `bass` | Boost the low end. *(DJ)* |
| `m!clearfilters` | `cf`, `resetfilters`, `nofilter` | Turn every effect off. *(DJ)* |
| `m!daycore` | — | Slowed and lower pitched. *(DJ)* |
| `m!distortion` | — | Crunchy bit-crushed distortion. *(DJ)* |
| `m!echo` | — | Add an echo. *(DJ)* |
| `m!filters` | `filter`, `effects` | List the active effects. |
| `m!karaoke` | — | Try to strip the vocals out. *(DJ)* |
| `m!muffle` | — | Muffle the audio, as if heard through a wall. *(DJ)* |
| `m!nightcore` | `nc` | Faster and higher pitched. *(DJ)* |
| `m!phone` | — | Tinny telephone-speaker effect. *(DJ)* |
| `m!pitch [0.5-2.0]` | — | Shift pitch without changing speed. *(DJ)* |
| `m!reverb` | — | Add a room reverb. *(DJ)* |
| `m!speed [0.25-4.0]` | `tempo` | Change playback speed without changing pitch. *(DJ)* |
| `m!treble [-20 to 20]` | — | Boost or cut the high end. *(DJ)* |
| `m!tremolo` | — | Wobbling volume effect. *(DJ)* |
| `m!vaporwave` | `vw`, `slowed` | Slowed and reverbed. *(DJ)* |
| `m!vibrato` | — | Wobbling pitch effect. *(DJ)* |

### Playlists

| Command | Aliases | Description |
| --- | --- | --- |
| `m!favourites [add\|remove\|list\|play] [song]` | `favorites`, `fav`, `favs`, `favourite`, `favorite` | Your personal favourites list. |
| `m!playlist <create\|delete\|list\|show\|add\|remove\|play\|rename\|clear> [name] [song]` | `pl`, `playlists` | Manage your saved playlists. |

### Settings

| Command | Aliases | Description |
| --- | --- | --- |
| `m!247 [on\|off]` | `24/7`, `24_7`, `stay` | Stay in the voice channel even when idle. *(DJ)* |
| `m!announce [on\|off]` | `nowplayingmessages` | Toggle the 'Now playing' messages. *(DJ)* |
| `m!dj [@role\|off]` | `djrole` | Set the DJ role that gates playback commands. |
| `m!prefix [new prefix]` | — | Show or change the command prefix for this server. *(DJ)* |
| `m!settings` | `setup`, `config` | Show this server's configuration. |

### General

| Command | Aliases | Description |
| --- | --- | --- |
| `m!help [command]` | `h`, `commands`, `cmds` | Show the command list, or details for one command. |
| `m!info` | `about`, `stats`, `botinfo` | Show bot and runtime information. |
| `m!lyrics [song]` | `ly` | Look up lyrics for the current or a named track. |
| `m!ping` | — | Check that the bot is responsive. |

---

## Sources

| Source | Support |
| --- | --- |
| YouTube (tracks, playlists, mixes, live) | full |
| SoundCloud, Bandcamp, Vimeo, Twitch, direct audio URLs | full, via yt-dlp |
| Spotify tracks / albums / playlists | metadata read, audio played from YouTube |
| Chat attachments | upload a file with `m!play` and it plays |
| Local files and folders | opt-in via `ALLOW_LOCAL_FILES` |

Spotify needs `SPOTIFY_CLIENT_ID` / `SPOTIFY_CLIENT_SECRET` (a free app from the
[Spotify dashboard](https://developer.spotify.com/dashboard)). Without them, Spotify links are
rejected with an explanation rather than silently failing.

Large playlists load lazily: only the metadata is fetched up front, and each track's stream URL is
resolved just before it plays. Queueing a 500-track playlist is one request, not 500.

---

## Notes for tabletop use

- `m!247` keeps the bot in the channel between scenes so it never drops mid-session.
- `m!loop queue` on an ambience playlist runs all night; `m!loop` alone repeats a single track.
- `m!playlist create tavern` then `m!playlist add tavern <link>` builds reusable scene playlists.
  They belong to *you*, not the server, so they follow you between games.
- `m!volume 30` ducks music under narration and takes effect on the current frame — no restart.
- Effects (`m!muffle`, `m!echo`, `m!reverb`) re-encode the track, so expect a brief gap when you
  toggle one.

---

## DJ permissions

Commands marked *(DJ)* are unrestricted until you actually configure a DJ role — a fresh install
lets everyone play, skip and pause. Once you run `m!dj @role`, those commands require the role,
**Manage Channels**, or being the only listener in the channel. `m!skip` always stays available:
non-DJs start a majority vote, while DJs and whoever queued the track skip outright.

---

## Configuration

Everything is environment variables; see [`.env.example`](.env.example) for the annotated list.
The ones worth knowing:

| Variable | Default | Purpose |
| --- | --- | --- |
| `FLUXER_TOKEN` | — | Bot token (required) |
| `FLUXER_API_URL` | fluxer.app | Your instance's API base |
| `BOT_PREFIX` | `m!` | Default prefix; overridable per server |
| `DEFAULT_VOLUME` / `MAX_VOLUME` | 100 / 200 | Starting and maximum volume |
| `IDLE_TIMEOUT` | 300 | Seconds before leaving an idle channel (0 = never) |
| `EMPTY_CHANNEL_TIMEOUT` | 60 | Seconds before leaving an empty channel (0 = never) |
| `PLAYLIST_LIMIT` | 500 | Max tracks pulled from one playlist |
| `YTDLP_COOKIEFILE` | — | Cookie file for age-gated videos |
| `ALLOW_LOCAL_FILES` | false | Let commands read the bot's own disk |

### Running several instances

Jockie users often run Jockie Music 2, 3, … so different groups can listen to different things.
Do the same here: one container per bot account, each with its own token, `BOT_PREFIX`
(`mm!`, `mmm!`) and data volume. See the commented block in `docker-compose.yml`.

---

## Development

```bash
pip install -e ".[dev]"
pytest
```

The suite covers the queue state machine, persistence, command routing, and the audio pipeline
end to end — real ffmpeg decoding, seeking, filters and frame pacing against a real LiveKit
`AudioSource`. Audio tests skip automatically when ffmpeg is absent; set `FFMPEG_PATH` to point
at a specific build.

```
jockiefluxer/
├── audio.py      ffmpeg → PCM frames → LiveKit, filters, gain
├── player.py     per-guild queue state machine and playback driver
├── bot.py        gateway events, permissions, prefixes
├── sources/      yt-dlp, Spotify, local files
├── commands/     the Jockie-compatible command surface
├── store.py      SQLite: settings, playlists, favourites
└── ui.py         embeds
```

## Troubleshooting

**"ffmpeg executable not found"** — install ffmpeg, or set `FFMPEG_PATH`.

**Bot joins but nothing plays** — check the logs for a yt-dlp error. YouTube sometimes demands a
signed-in client; supply `YTDLP_COOKIEFILE`. Keep yt-dlp current (`pip install -U yt-dlp`);
extraction breaks whenever YouTube changes, and that is the usual cause.

**Commands are ignored** — the **Message Content** intent is almost always the reason. Confirm the
prefix with an @mention: `@bot help` works regardless of prefix.

## License

MIT
