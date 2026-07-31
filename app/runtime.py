from __future__ import annotations
import json
import logging
from pathlib import Path
from typing import Any
import discord
from discord.ext import commands
from config import STAFF_CHANNEL_ID
from services.sheets_resilience import install_gspread_resilience
from services.sheets_service import SheetsService
from services.league_service import LeagueService
from services.robincon_service import RobinConService

install_gspread_resilience()
BASE_DIR=Path(__file__).resolve().parent.parent
DATA_DIR=BASE_DIR/'data'
DATA_DIR.mkdir(parents=True,exist_ok=True)
logger=logging.getLogger(__name__)
intents=discord.Intents.default(); intents.message_content=True
bot=commands.Bot(command_prefix='!', intents=intents, help_command=None,
                 status=discord.Status.online, activity=discord.Game(name='Managing preorders'))
PENDING_REQUESTS_FILE=DATA_DIR/'pending_requests.json'

def load_pending_requests() -> dict[int,dict[str,Any]]:
    if not PENDING_REQUESTS_FILE.exists(): return {}
    try:
        raw=json.loads(PENDING_REQUESTS_FILE.read_text(encoding='utf-8'))
        if not isinstance(raw,dict): raise ValueError('Pending request data must be a JSON object')
        result={int(k):v for k,v in raw.items() if isinstance(v,dict)}
        logger.info('Loaded %s pending approval request(s)',len(result)); return result
    except Exception:
        logger.exception('Could not load pending approvals from %s',PENDING_REQUESTS_FILE); return {}

def save_pending_requests() -> None:
    tmp=PENDING_REQUESTS_FILE.with_suffix('.json.tmp')
    try:
        tmp.write_text(json.dumps(pending_requests,indent=2),encoding='utf-8'); tmp.replace(PENDING_REQUESTS_FILE)
    except OSError: logger.exception('Could not save pending approvals')

pending_requests=load_pending_requests()
pending_quantity_requests: dict[int,dict[str,Any]]={}
customer_baskets: dict[int,list[dict[str,Any]]]={}
try:
    sheets=SheetsService(); league_service=LeagueService(sheets)
except Exception:
    logger.exception('Google Sheets connection failed'); sheets=None; league_service=None
try:
    robincon_service=RobinConService()
except Exception:
    logger.exception('RobinCon connection failed'); robincon_service=None

def is_staff_channel(ctx: commands.Context) -> bool:
    return ctx.guild is not None and ctx.channel.id == STAFF_CHANNEL_ID
