from __future__ import annotations
import logging
import discord
from discord import app_commands
from discord.ext import tasks
from config import LEAGUE_GUILD_ID, LEAGUE_CHANNEL_ID, LEAGUE_EVENT_DURATION_HOURS, LEAGUE_ROLE_ID, LEAGUE_WINDOW_DAYS, STAFF_ROLE_ID
from app.runtime import bot, league_service
logger=logging.getLogger(__name__)

def get_league_guild() -> discord.Guild | None:
    """Return the configured Robins guild when available."""

    return bot.get_guild(LEAGUE_GUILD_ID)


async def has_league_role(discord_user_id: int) -> bool:
    """Return whether a Discord user currently has the League role."""

    guild = get_league_guild()
    if guild is None:
        logger.error(
            "Could not verify League access because guild %s is unavailable",
            LEAGUE_GUILD_ID,
        )
        return False

    member = guild.get_member(discord_user_id)

    if member is None:
        try:
            member = await guild.fetch_member(discord_user_id)
        except discord.NotFound:
            logger.info(
                "League access denied because user %s is not in the guild",
                discord_user_id,
            )
            return False
        except discord.HTTPException:
            logger.exception(
                "Could not verify League role for user %s",
                discord_user_id,
            )
            return False

    return any(
        role.id == LEAGUE_ROLE_ID
        for role in member.roles
    )


async def validate_league_staff(
    interaction: discord.Interaction,
) -> discord.Member | None:
    """Validate guild, channel, staff role, and League availability."""

    if interaction.guild_id != LEAGUE_GUILD_ID:
        await interaction.response.send_message(
            "❌ This command can only be used in the Robins server.",
            ephemeral=True,
        )
        return None

    if interaction.channel_id != LEAGUE_CHANNEL_ID:
        await interaction.response.send_message(
            "❌ Use this command in the League check-in channel.",
            ephemeral=True,
        )
        return None

    member = interaction.user
    if not isinstance(member, discord.Member):
        await interaction.response.send_message(
            "❌ I could not verify your server roles.",
            ephemeral=True,
        )
        return None

    if not any(role.id == STAFF_ROLE_ID for role in member.roles):
        await interaction.response.send_message(
            "❌ Only Robins staff can use this command.",
            ephemeral=True,
        )
        return None

    if league_service is None:
        await interaction.response.send_message(
            "❌ The League service is currently unavailable.",
            ephemeral=True,
        )
        return None

    return member


async def apply_league_role(discord_user_id: int) -> bool:
    """Add the configured League role to one guild member."""

    guild = get_league_guild()
    if guild is None or league_service is None:
        return False

    role = guild.get_role(LEAGUE_ROLE_ID)
    if role is None:
        logger.error("Configured League role %s was not found", LEAGUE_ROLE_ID)
        return False

    member = guild.get_member(discord_user_id)
    if member is None:
        try:
            member = await guild.fetch_member(discord_user_id)
        except discord.HTTPException:
            logger.warning(
                "Could not retrieve League member %s",
                discord_user_id,
            )
            return False

    if role not in member.roles:
        try:
            await member.add_roles(
                role,
                reason="Robins League attendance check-in",
            )
        except discord.HTTPException:
            logger.exception(
                "Could not add League role to member %s",
                discord_user_id,
            )
            return False

    league_service.set_role_active(discord_user_id, True)
    return True


@app_commands.command(
    name="linkplayer",
    description="Link your Play! Pokémon Player ID to Discord.",
)
@app_commands.describe(player_id="Your Play! Pokémon Player ID")
async def link_player_command(
    interaction: discord.Interaction,
    player_id: str,
) -> None:
    """Link a Discord user to a Play! Pokémon Player ID."""

    if league_service is None:
        await interaction.response.send_message(
            "❌ The League service is currently unavailable.",
            ephemeral=True,
        )
        return

    await interaction.response.defer(ephemeral=True)

    try:
        linked = league_service.link_player(
            discord_user_id=interaction.user.id,
            discord_name=str(interaction.user),
            player_id=player_id,
        )
    except ValueError as exc:
        await interaction.followup.send(f"❌ {exc}", ephemeral=True)
        return
    except Exception:
        logger.exception("Failed to link League player")
        await interaction.followup.send(
            "❌ Your Player ID could not be linked.",
            ephemeral=True,
        )
        return

    await interaction.followup.send(
        (
            "✅ **Player ID linked.**\n\n"
            f"Player ID: `{linked['player_id']}`\n\n"
            "You can now check in when a Robins League event is active."
        ),
        ephemeral=True,
    )


@app_commands.command(
    name="unlinkplayer",
    description="Remove your linked Play! Pokémon Player ID.",
)
async def unlink_player_command(
    interaction: discord.Interaction,
) -> None:
    """Remove a user's Player ID link and League role."""

    if league_service is None:
        await interaction.response.send_message(
            "❌ The League service is currently unavailable.",
            ephemeral=True,
        )
        return

    await interaction.response.defer(ephemeral=True)

    try:
        unlinked = league_service.unlink_player(interaction.user.id)
    except ValueError as exc:
        await interaction.followup.send(f"❌ {exc}", ephemeral=True)
        return
    except Exception:
        logger.exception("Failed to unlink League player")
        await interaction.followup.send(
            "❌ Your Player ID could not be unlinked.",
            ephemeral=True,
        )
        return

    guild = get_league_guild()
    if guild is not None:
        role = guild.get_role(LEAGUE_ROLE_ID)
        member = guild.get_member(interaction.user.id)
        if member is None:
            try:
                member = await guild.fetch_member(interaction.user.id)
            except discord.HTTPException:
                member = None

        if role is not None and member is not None and role in member.roles:
            try:
                await member.remove_roles(
                    role,
                    reason="League Player ID unlinked",
                )
            except discord.HTTPException:
                logger.warning(
                    "Could not remove League role from unlinked member %s",
                    interaction.user.id,
                )

    await interaction.followup.send(
        (
            "✅ **Player ID unlinked.**\n\n"
            f"Removed Player ID: `{unlinked.get('Player ID', 'Unknown')}`"
        ),
        ephemeral=True,
    )


@app_commands.command(
    name="leaguecheckin",
    description="Check in to the active Robins League event.",
)
@app_commands.describe(store_code="The store code displayed inside Robins")
async def league_checkin_command(
    interaction: discord.Interaction,
    store_code: str,
) -> None:
    """Check a linked player into an active League event."""

    if league_service is None:
        await interaction.response.send_message(
            "❌ The League service is currently unavailable.",
            ephemeral=True,
        )
        return

    await interaction.response.defer(ephemeral=True)

    try:
        result = league_service.check_in_player(
            discord_user_id=interaction.user.id,
            store_code=store_code,
        )
    except ValueError as exc:
        await interaction.followup.send(f"❌ {exc}", ephemeral=True)
        return
    except Exception:
        logger.exception("League check-in failed")
        await interaction.followup.send(
            "❌ Your League check-in could not be completed.",
            ephemeral=True,
        )
        return

    role_added = await apply_league_role(interaction.user.id)
    role_message = (
        "Your League Player role has been added or renewed."
        if role_added
        else "Your attendance was recorded, but the role could not be updated."
    )

    await interaction.followup.send(
        (
            "✅ **League check-in complete.**\n\n"
            f"Event ID: `{result['event_id']}`\n"
            f"Player ID: `{result['player_id']}`\n\n"
            f"{role_message}"
        ),
        ephemeral=True,
    )


@app_commands.command(
    name="leaguestatus",
    description="View your Robins League membership status.",
)
async def player_league_status_command(
    interaction: discord.Interaction,
) -> None:
    """Show a player's linked ID and latest attendance."""

    if league_service is None:
        await interaction.response.send_message(
            "❌ The League service is currently unavailable.",
            ephemeral=True,
        )
        return

    try:
        player = league_service.get_linked_player(interaction.user.id)
    except Exception:
        logger.exception("Could not read player League status")
        await interaction.response.send_message(
            "❌ Your League status could not be retrieved.",
            ephemeral=True,
        )
        return

    if player is None:
        await interaction.response.send_message(
            "You have not linked a Play! Pokémon Player ID yet.",
            ephemeral=True,
        )
        return

    await interaction.response.send_message(
        (
            "**Your Robins League Status**\n\n"
            f"Player ID: `{player.get('Player ID', 'Unknown')}`\n"
            f"Last Attendance: "
            f"`{player.get('Last Attendance') or 'No attendance recorded'}`\n"
            f"Role Active: `{player.get('Role Active', 'FALSE')}`"
        ),
        ephemeral=True,
    )


bot.tree.add_command(link_player_command)
bot.tree.add_command(unlink_player_command)
bot.tree.add_command(league_checkin_command)
bot.tree.add_command(player_league_status_command)


league_group = app_commands.Group(
    name="league",
    description="Manage Robins Pokémon League events.",
    guild_ids=[LEAGUE_GUILD_ID],
)


@league_group.command(
    name="start",
    description="Start a new Robins League event.",
)
async def league_start(
    interaction: discord.Interaction,
) -> None:
    """Start a League event and publish its store check-in code."""

    if await validate_league_staff(interaction) is None:
        return

    await interaction.response.defer(ephemeral=True)

    try:
        event = league_service.start_event()
    except ValueError as exc:
        await interaction.followup.send(f"❌ {exc}", ephemeral=True)
        return
    except Exception:
        logger.exception("Failed to start League event")
        await interaction.followup.send(
            "❌ The League event could not be started.",
            ephemeral=True,
        )
        return

    channel = bot.get_channel(LEAGUE_CHANNEL_ID)
    if not isinstance(channel, discord.TextChannel):
        await interaction.followup.send(
            "❌ The event started, but the League channel was not found.",
            ephemeral=True,
        )
        return

    await channel.send(
        (
            "**League event started.**\n\n"
            f"**Event ID:** `{event['event_id']}`\n"
            f"**Store Code:** `{event['store_code']}`\n\n"
            f"This event expires in {LEAGUE_EVENT_DURATION_HOURS} hours."
        )
    )

    await interaction.followup.send(
        "✅ League event started and the store code was posted.",
        ephemeral=True,
    )


@league_group.command(
    name="end",
    description="End the active Robins League event.",
)
async def league_end(
    interaction: discord.Interaction,
) -> None:
    """End the currently active League event."""

    if await validate_league_staff(interaction) is None:
        return

    await interaction.response.defer(ephemeral=True)

    try:
        event = league_service.close_active_event()
    except ValueError as exc:
        await interaction.followup.send(f"❌ {exc}", ephemeral=True)
        return
    except Exception:
        logger.exception("Failed to end League event")
        await interaction.followup.send(
            "❌ The League event could not be ended.",
            ephemeral=True,
        )
        return

    channel = bot.get_channel(LEAGUE_CHANNEL_ID)
    if isinstance(channel, discord.TextChannel):
        await channel.send(
            (
                "**League event ended.**\n\n"
                f"**Event ID:** `{event.get('Event ID', 'Unknown')}`\n\n"
                "Players can no longer check in."
            )
        )

    await interaction.followup.send(
        "✅ League event ended.",
        ephemeral=True,
    )


@league_group.command(
    name="status",
    description="Show the current Robins League status.",
)
async def league_status(
    interaction: discord.Interaction,
) -> None:
    """Show event, attendance, and linked-player totals."""

    if await validate_league_staff(interaction) is None:
        return

    await interaction.response.defer(ephemeral=True)

    try:
        status = league_service.get_league_status()
    except Exception:
        logger.exception("Failed to retrieve League status")
        await interaction.followup.send(
            "❌ League status could not be retrieved.",
            ephemeral=True,
        )
        return

    event = status["active_event"]
    if event is None:
        description = (
            "**Robins League Status**\n\n"
            "Active Event: `No`\n"
            f"Linked Players: `{status['linked_players']}`\n"
            f"Active League Players: `{status['active_players']}`"
        )
    else:
        description = (
            "**Robins League Status**\n\n"
            "Active Event: `Yes`\n"
            f"Event ID: `{event.get('Event ID', 'Unknown')}`\n"
            f"Store Code: `{event.get('Store Code', 'Unknown')}`\n"
            f"Players Checked In: `{status['attendance_count']}`\n"
            f"Linked Players: `{status['linked_players']}`\n"
            f"Active League Players: `{status['active_players']}`\n"
            f"Closes: `{event.get('End Time', 'Unknown')}`"
        )

    await interaction.followup.send(description, ephemeral=True)


@league_group.command(
    name="checkin",
    description="Manually check a member into the active League event.",
)
@app_commands.describe(member="The Discord member attending League")
async def league_staff_checkin(
    interaction: discord.Interaction,
    member: discord.Member,
) -> None:
    """Allow staff to record attendance without a store code."""

    if await validate_league_staff(interaction) is None:
        return

    player = league_service.get_linked_player(member.id)
    if player is None:
        await interaction.response.send_message(
            "❌ That member has not linked a Player ID.",
            ephemeral=True,
        )
        return

    active_event = league_service.get_active_event()
    if active_event is None:
        await interaction.response.send_message(
            "❌ There is no active League event.",
            ephemeral=True,
        )
        return

    store_code = str(active_event.get("Store Code", ""))
    await interaction.response.defer(ephemeral=True)

    try:
        result = league_service.check_in_player(member.id, store_code)
    except ValueError as exc:
        await interaction.followup.send(f"❌ {exc}", ephemeral=True)
        return
    except Exception:
        logger.exception("Staff League check-in failed")
        await interaction.followup.send(
            "❌ The member could not be checked in.",
            ephemeral=True,
        )
        return

    role_added = await apply_league_role(member.id)
    await interaction.followup.send(
        (
            f"✅ {member.mention} checked in to event "
            f"`{result['event_id']}`.\n"
            f"Role updated: `{'Yes' if role_added else 'No'}`"
        ),
        ephemeral=True,
    )


bot.tree.add_command(league_group)



@tasks.loop(hours=24)
async def reconcile_league_roles() -> None:
    """Reconcile League roles with one player read and one state write."""

    if league_service is None:
        return

    guild = get_league_guild()
    if guild is None:
        logger.warning("League role reconciliation skipped: guild unavailable")
        return

    role = guild.get_role(LEAGUE_ROLE_ID)
    if role is None:
        logger.error("League role reconciliation skipped: role unavailable")
        return

    try:
        players = league_service.get_role_reconciliation_players()
    except Exception:
        logger.exception("Could not load League players for reconciliation")
        return

    state_updates: list[tuple[int, bool]] = []
    role_changes = 0
    departed = 0
    skipped = 0

    for player in players:
        user_id = player["discord_user_id"]
        should_have_role = player["should_have_role"]
        stored_active = player["role_active"]
        member = guild.get_member(user_id)
        if member is None:
            try:
                member = await guild.fetch_member(user_id)
            except discord.NotFound:
                departed += 1
                if stored_active:
                    state_updates.append((player["row_number"], False))
                continue
            except discord.HTTPException:
                skipped += 1
                logger.warning(
                    "Could not retrieve member %s during role reconciliation",
                    user_id,
                )
                continue

        has_role = role in member.roles
        try:
            if should_have_role and not has_role:
                await member.add_roles(
                    role,
                    reason=f"League attendance within {LEAGUE_WINDOW_DAYS} days",
                )
                role_changes += 1
            elif not should_have_role and has_role:
                await member.remove_roles(
                    role,
                    reason=f"No League attendance within {LEAGUE_WINDOW_DAYS} days",
                )
                role_changes += 1
        except discord.HTTPException:
            skipped += 1
            logger.exception(
                "Could not reconcile League role for member %s",
                user_id,
            )
            continue

        if stored_active != should_have_role:
            state_updates.append((player["row_number"], should_have_role))

    try:
        persisted = league_service.set_role_states_bulk(state_updates)
    except Exception:
        persisted = 0
        logger.exception("Could not persist League role reconciliation state")

    logger.info(
        "League reconciliation complete: %s players checked, %s role changes, "
        "%s state updates, %s departed members, %s skipped",
        len(players),
        role_changes,
        persisted,
        departed,
        skipped,
    )


@reconcile_league_roles.before_loop
async def before_reconcile_league_roles() -> None:
    await bot.wait_until_ready()


slash_commands_synced = False


