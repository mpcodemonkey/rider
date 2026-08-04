"""Audio effect commands.

Effects are applied by re-encoding through ffmpeg, so toggling one restarts the
current track at its current position — expect a brief pause, then the effect.
"""

from __future__ import annotations

from .. import ui
from ..audio import BASSBOOST_LEVELS, FILTER_BUILDERS, NUMERIC_FILTERS
from .core import CommandError, Context, command

CATEGORY = "Filters"


async def _toggle(context: Context, name: str, value: float = 1.0) -> None:
    """Turn a named effect on or off and re-encode the current track."""
    player = await context.require_player()

    if name in player.filters:
        del player.filters[name]
        enabled = False
    else:
        player.filters[name] = value
        enabled = True

    await player.apply_filters()
    state = "enabled" if enabled else "disabled"
    await context.ok(f"🎚️ **{name.capitalize()}** {state}.")


def _numeric_value(context: Context, name: str) -> float:
    """Read and clamp the numeric argument for effects that take one."""
    default, low, high = NUMERIC_FILTERS[name]
    if not context.argument:
        return default
    try:
        value = float(context.argument)
    except ValueError:
        raise CommandError(
            f"Usage: `{context.prefix}{name} <{low} to {high}>`"
        ) from None
    if not low <= value <= high:
        raise CommandError(f"Pick a value between **{low}** and **{high}**.")
    return value


@command(
    "bassboost",
    aliases=("bb", "bass"),
    usage="[off|low|medium|high|extreme]",
    description="Boost the low end.",
    category=CATEGORY,
    dj_only=True,
)
async def bassboost(context: Context) -> None:
    player = await context.require_player()
    argument = context.argument.lower().strip()

    if not argument:
        if "bassboost" in player.filters:
            del player.filters["bassboost"]
            await player.apply_filters()
            await context.ok("🎚️ Bass boost **disabled**.")
            return
        argument = "medium"

    if argument in BASSBOOST_LEVELS:
        gain = BASSBOOST_LEVELS[argument]
    else:
        try:
            gain = float(argument)
        except ValueError:
            levels = ", ".join(f"`{name}`" for name in BASSBOOST_LEVELS)
            raise CommandError(f"Pick one of: {levels} (or a dB value).") from None

    if gain == 0:
        player.filters.pop("bassboost", None)
        await player.apply_filters()
        await context.ok("🎚️ Bass boost **disabled**.")
        return

    player.filters["bassboost"] = gain
    await player.apply_filters()
    await context.ok(f"🎚️ Bass boost set to **{argument}** ({gain:g} dB).")


@command(
    "speed",
    aliases=("tempo",),
    usage="[0.25-4.0]",
    description="Change playback speed without changing pitch.",
    category=CATEGORY,
    dj_only=True,
)
async def speed(context: Context) -> None:
    player = await context.require_player()
    value = _numeric_value(context, "speed")
    if value == 1.0:
        player.filters.pop("speed", None)
    else:
        player.filters["speed"] = value
    await player.apply_filters()
    await context.ok(f"⏩ Speed set to **{value:g}x**.")


@command(
    "pitch",
    usage="[0.5-2.0]",
    description="Shift pitch without changing speed.",
    category=CATEGORY,
    dj_only=True,
)
async def pitch(context: Context) -> None:
    player = await context.require_player()
    value = _numeric_value(context, "pitch")
    if value == 1.0:
        player.filters.pop("pitch", None)
    else:
        player.filters["pitch"] = value
    await player.apply_filters()
    await context.ok(f"🎵 Pitch set to **{value:g}x**.")


@command("nightcore", aliases=("nc",), description="Faster and higher pitched.", category=CATEGORY, dj_only=True)
async def nightcore(context: Context) -> None:
    await _toggle(context, "nightcore")


@command("vaporwave", aliases=("vw", "slowed"), description="Slowed and reverbed.", category=CATEGORY, dj_only=True)
async def vaporwave(context: Context) -> None:
    await _toggle(context, "vaporwave")


@command("daycore", description="Slowed and lower pitched.", category=CATEGORY, dj_only=True)
async def daycore(context: Context) -> None:
    await _toggle(context, "daycore")


@command("8d", aliases=("eightd", "rotation"), description="Rotating stereo effect.", category=CATEGORY, dj_only=True)
async def eight_d(context: Context) -> None:
    await _toggle(context, "8d")


@command("tremolo", description="Wobbling volume effect.", category=CATEGORY, dj_only=True)
async def tremolo(context: Context) -> None:
    await _toggle(context, "tremolo", NUMERIC_FILTERS["tremolo"][0])


@command("vibrato", description="Wobbling pitch effect.", category=CATEGORY, dj_only=True)
async def vibrato(context: Context) -> None:
    await _toggle(context, "vibrato", NUMERIC_FILTERS["vibrato"][0])


@command("karaoke", description="Try to strip the vocals out.", category=CATEGORY, dj_only=True)
async def karaoke(context: Context) -> None:
    await _toggle(context, "karaoke")


@command("echo", description="Add an echo.", category=CATEGORY, dj_only=True)
async def echo(context: Context) -> None:
    await _toggle(context, "echo")


@command("reverb", description="Add a room reverb.", category=CATEGORY, dj_only=True)
async def reverb(context: Context) -> None:
    await _toggle(context, "reverb")


@command("muffle", description="Muffle the audio, as if heard through a wall.", category=CATEGORY, dj_only=True)
async def muffle(context: Context) -> None:
    await _toggle(context, "muffle")


@command("phone", description="Tinny telephone-speaker effect.", category=CATEGORY, dj_only=True)
async def phone(context: Context) -> None:
    await _toggle(context, "phone")


@command("distortion", description="Crunchy bit-crushed distortion.", category=CATEGORY, dj_only=True)
async def distortion(context: Context) -> None:
    await _toggle(context, "distortion")


@command("treble", usage="[-20 to 20]", description="Boost or cut the high end.", category=CATEGORY, dj_only=True)
async def treble(context: Context) -> None:
    player = await context.require_player()
    value = _numeric_value(context, "treble")
    if value == 0:
        player.filters.pop("treble", None)
    else:
        player.filters["treble"] = value
    await player.apply_filters()
    await context.ok(f"🎚️ Treble set to **{value:g} dB**.")


@command(
    "filters",
    aliases=("filter", "effects"),
    description="List the active effects.",
    category=CATEGORY,
)
async def filters(context: Context) -> None:
    player = await context.require_player()
    if not player.filters:
        available = ", ".join(f"`{name}`" for name in sorted(FILTER_BUILDERS))
        await context.reply(
            embed=ui.info(
                f"No effects are active.\n\n**Available:** {available}\n"
                f"Turn one on with `{context.prefix}<name>`."
            )
        )
        return

    active = "\n".join(
        f"• **{name}**" + (f" — `{value:g}`" if name in NUMERIC_FILTERS else "")
        for name, value in sorted(player.filters.items())
    )
    await context.reply(
        embed=ui.info(
            f"**Active effects**\n{active}\n\n"
            f"Clear them all with `{context.prefix}clearfilters`."
        )
    )


@command(
    "clearfilters",
    aliases=("cf", "resetfilters", "nofilter"),
    description="Turn every effect off.",
    category=CATEGORY,
    dj_only=True,
)
async def clearfilters(context: Context) -> None:
    player = await context.require_player()
    if not player.filters:
        raise CommandError("No effects are active.")
    player.filters.clear()
    await player.apply_filters()
    await context.ok("🎚️ Cleared all effects.")
