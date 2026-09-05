#!/usr/bin/env python3
"""
action_followup.py - Intelligent Customer Follow-Up & Language Analysis Engine
Performs pre-outreach context analysis across WhatsApp conversation history & CRM notes,
determines the customer's preferred communication language (Afrikaans vs. English),
enforces Dealership OS guardrails ({SALESPERSON_NAME} sender identity, strict long dash ban, 1-2 sentences),
dispatches via the JAX WhatsApp Monitor bridge, and dual-logs the outcome to dealership CRM.
"""

import sys
import os
import re
import json
import time
import argparse
import sqlite3
import urllib.parse
from datetime import datetime, timedelta
import urllib.request
import urllib.error

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
AUTOHUB_SCRIPTS_DIR = os.getenv("AUTOHUB_SCRIPTS_DIR", os.path.abspath(os.path.join(SCRIPT_DIR, "../../autohub-portal/scripts")))
sys.path.append(AUTOHUB_SCRIPTS_DIR)
sys.path.append(SCRIPT_DIR)
from customer_identity import lookup_customer, normalize_phone

try:
    from action_prospect import action_prospect, find_prospect_in_db
    from prospect_db import DB_PATH as CRM_DB_PATH
except ImportError:
    action_prospect = None
    find_prospect_in_db = None
    CRM_DB_PATH = "data/scratch/prospect_history.db"

WA_DB_PATH = "jax-shared/data/prospects.db"
MONITOR_API_BASE = "http://127.0.0.1:9095"

SALESPERSON_NAME = os.getenv("SALESPERSON_NAME", "Sales Executive")
DEALERSHIP_NAME = os.getenv("DEALERSHIP_NAME", "Dealership")
CRM_USERNAME = os.getenv("CRM_USERNAME", "")
CRM_USERNAME_SHORT = CRM_USERNAME.split()[0] if CRM_USERNAME else ""
CRM_USERNAME_LAST = CRM_USERNAME.split()[-1] if len(CRM_USERNAME.split()) > 1 else ""

# Comprehensive South African Indigenous African Names and Surnames
AFRICAN_FIRST_NAMES = {
    'duduzile', 'dudu', 'sipho', 'thabo', 'nomvula', 'bongani', 'nthabiseng', 'kagiso',
    'lerato', 'tebogo', 'khanyisile', 'zanele', 'bongiwe', 'sifiso', 'mpho', 'sinethemba',
    'ntshuxeko', 'thato', 'lydia', 'nkosana', 'joseph', 'judas', 'zwelithini', 'mfundisi',
    'elizabeth', 'lorraine', 'paulinah', 'given', 'nkosikhona', 'itumeleng', 'mawande',
    'mawina', 'tracey', 'annikie', 'mecha', 'samuel', 'gershom', 'nkamoheleng', 'jonas',
    'skhombuzo', 'mamokone', 'matlhatsi', 'khathu', 'balbina', 'jane', 'neo', 'sophy',
    'lindiwe', 'busisiwe', 'themba', 'mandla', 'vusi', 'dumi', 'simphiwe', 'mbali',
    'ayanda', 'bandile', 'senzo', 'lungelo', 'sibusiso', 'phumzile', 'nonhlanhla', 'refilwe',
    'lebogang', 'dineo', 'tshepo', 'karabo', 'katlego', 'kabelo', 'kgotso', 'lesego',
    'dimakatso', 'puleng', 'palesa', 'keabetswe', 'koketso', 'boitumelo', 'sibongile',
    'nomsa', 'thandiwe', 'thandi', 'zodwa', 'zola', 'nokuthula', 'gugu', 'precious',
    'thobile', 'thandeka', 'nokwanda', 'sindisiwe', 'slindile', 'hlengiwe', 'nompumelelo',
    'mpume', 'babalwa', 'funeka', 'nonkanyiso', 'zintle', 'asanda', 'bulelwa', 'unathi',
    'noluthando', 'siphokazi', 'nomfundo', 'nontando', 'nontobeko', 'khethiwe', 'makhosi',
    'minenhle', 'aphiwe', 'andiswa', 'mihlali', 'siyamthanda', 'onkarabile', 'reatlegile',
    'malebo', 'boipelo', 'tshegofatso', 'naledi', 'bokamoso', 'amogelang', 'oratile',
    'tshepang', 'bontle', 'masego', 'rorisang', 'keneilwe', 'kgomotso', 'matshepo',
    'mphoentle', 'mamello', 'molebogeng', 'tebatso', 'mahlatse', 'makgabo', 'khomotso',
    'morongwa', 'mokgadi', 'tsakani', 'kulani', 'ntsako', 'vonani', 'tintswalo', 'nhlalala',
    'hlamalani', 'langavi', 'rhulani', 'ntshembo', 'khensani', 'nsovo', 'ntsumi', 'vutomi',
    'vutlhari', 'ntsakisi', 'tinyiko', 'rirhandzu', 'dzunisani', 'nwanati', 'ndivhuwo',
    'mulalo', 'takalani', 'rendani', 'vhahangwele', 'tshilidzi', 'zwivhuya', 'rabelani',
    'fulufhelo', 'fhulufhelo', 'dakalo', 'rolivhuwa', 'elelwani', 'livhuwani', 'rotondwa',
    'vhutshilo', 'muofhe', 'mashudu', 'lufuno', 'khathutshelo', 'mmbengeni', 'nkosinathi',
    'thabiso', 'mzwandile', 'siyabonga', 'jabulani', 'bheki', 'bhekisisa', 'sandile',
    'nhlanhla', 'menzi', 'sphiwe', 'mthokozisi', 'philani', 'xolani', 'mlungisi', 'lwazi',
    'andile', 'anele', 'luyanda', 'wandile', 'ayakhanya', 'sive', 'lwandle', 'melokuhle',
    'kwanele', 'musawenkosi', 'nhlakanipho', 'sizwe', 'dumisani', 'mthunzi', 'vuyo',
    'luvuyo', 'zolani', 'loyiso', 'sonwabo', 'akhona', 'yamkela', 'avela', 'siyanda',
    'olwethu', 'thulani', 'khaya', 'mkhululi', 'mongezi', 'lonwabo', 'babalo', 'luvo',
    'siseko', 'lulamile', 'thabang', 'tshepiso', 'tumelo', 'tumi', 'lesiba', 'lehlogonolo',
    'khumo', 'kutlwano', 'moeketsi', 'molefi', 'modise', 'mohau', 'moseki', 'motlatsi',
    'pule', 'rapelang', 'sello', 'tau', 'teboho', 'thapelo', 'tokelo', 'tshediso', 'tsepo',
    'tsietsi', 'dithebe', 'boikanyo', 'gontse', 'kgosi', 'kago', 'kegomoditswe', 'khumoetsile',
    'odirile', 'omphile', 'onalethata', 'onkgopotse', 'orefile', 'osegofetse', 'oteng',
    'phenyo', 'refentse', 'resego', 'tlotlo', 'mogomotsi', 'tiro', 'thuto', 'letlhogonolo',
    'boitshepo', 'malatji', 'matome', 'kgabo', 'lesetja', 'phetole', 'ngoako', 'mamabolo',
    'mashishi', 'mokgana', 'phaswane', 'phuti', 'ramokone', 'sebola', 'sekgobela',
    'senoamadi', 'seshoka', 'tlou', 'makgatho', 'mahlatsi', 'rasefate', 'ramphelane',
    'maphuti', 'tiisetso', 'morapedi', 'blessing', 'gift', 'prince', 'innocent', 'lucky',
    'promise', 'patience', 'faith', 'hope', 'grace', 'peace', 'mercy', 'joy', 'justice',
    'wisdom', 'bright', 'clever', 'goodness', 'wonder', 'marvel', 'shepherd', 'doctor', 'witness'
}

AFRICAN_SURNAMES = {
    'ngcobo', 'dlamini', 'zulu', 'ndlovu', 'khumalo', 'sithole', 'mthembu', 'molefe',
    'modise', 'baloyi', 'chauke', 'mabaso', 'mokoena', 'radebe', 'nkosi', 'manzini',
    'lieta', 'gumede', 'cele', 'buthelezi', 'zungu', 'ntuli', 'hlongwane', 'khoza',
    'sibiya', 'zwane', 'mkhize', 'mbatha', 'nxumalo', 'shabalala', 'masondo', 'hadebe',
    'bhengu', 'majola', 'mhlongo', 'zondi', 'gwala', 'maphumulo', 'mathebula', 'maluleke',
    'mabunda', 'rikhotso', 'nhlapo', 'mahlangu', 'sibanyoni', 'masombuka', 'skosana',
    'mtsweni', 'mnguni', 'mabena', 'morake', 'tsotetsi', 'motaung', 'moloi', 'mofokeng',
    'mosia', 'motloung', 'sebele', 'tau', 'phiri', 'ledwaba', 'mamabolo', 'mashishi',
    'mokwena', 'malatji', 'mogale', 'matlala', 'letsoalo', 'magaga', 'tjie', 'mocheke',
    'thsehlo', 'ratona', 'busi', 'moredi', 'nikelo', 'mawina', 'dikutle', 'makhwela',
    'ramorula', 'sepeng', 'polo', 'shili', 'zondo', 'mamokone', 'musutua', 'tswai',
    'khaaha', 'manaka', 'mndaba', 'moyo', 'sibanda', 'ncube', 'nkomo', 'tshuma', 'mpofu',
    'nyoni', 'dube', 'gumbo', 'ndaba', 'khanye', 'mtshali', 'vilakazi', 'ziqubu',
    'khuzwayo', 'mpanza', 'msimang', 'mthethwa', 'xulu', 'ngubane', 'langa', 'jiyane',
    'mvelase', 'fakude', 'mavuso', 'shabangu', 'lukhele', 'tsabedze', 'dladla', 'nene',
    'mchunu', 'sosibo', 'mkhwanazi', 'kunene', 'mncube', 'mnisi', 'mavimbela', 'nkuna',
    'ngoveni', 'shivambu', 'khosa', 'hlungwani', 'bila', 'mongwe', 'makukule', 'risenga',
    'maringa', 'maswanganyi', 'mabasa', 'ntimane', 'munyai', 'ramabulana', 'netshifhefhe',
    'nemudzivhadi', 'tshivhase', 'ravele', 'ramovha', 'nelwamondo', 'sinthumule', 'kutama',
    'madzivhandila', 'ligege', 'mphaphuli', 'tshikovhi', 'tshirando', 'mudau', 'singo',
    'mulaudzi', 'khorommbi', 'rambuda', 'maake', 'mabuela', 'madisha', 'makgoba', 'malahlela',
    'maleka', 'mametja', 'mangena', 'maphanga', 'maraba', 'masenya', 'mashego', 'mathabatha',
    'matlou', 'matsepe', 'mojapelo', 'moloto', 'morudu', 'moselakgomo', 'mothiba', 'mothapo',
    'mphahlele', 'nchabeleng', 'ngoepe', 'phasha', 'phatudi', 'ramahlale', 'rammutla', 'ratau',
    'seakamela', 'sebati', 'segooa', 'sekhukhune', 'selepe', 'selolo', 'semenya', 'senyolo',
    'seroka', 'sethunya', 'thobejane', 'tleane', 'dikutle', 'tswai', 'ramorula', 'shili'
}

AFRICAN_STEM_PREFIXES = (
    'nko', 'nts', 'nth', 'nom', 'non', 'nto', 'mph', 'mkh', 'mth', 'mzw', 'siy',
    'sim', 'sip', 'sif', 'skh', 'sbu', 'sibu', 'zwe', 'kga', 'kgo', 'tsh', 'leh',
    'kha', 'dudu', 'bong', 'bhek', 'lindi', 'busis', 'thab', 'teb', 'katl', 'dima',
    'pule', 'pale', 'moko', 'mofo', 'maba', 'mabu', 'mabe', 'mala', 'malu', 'math',
    'matl', 'maso', 'mash', 'balo', 'chau', 'rikh', 'muda', 'nets', 'nemu', 'itum',
    'boit', 'kabe', 'kgot', 'refi', 'refe', 'lebo', 'dine', 'kara', 'orat', 'amog',
    'keab', 'koke', 'tume', 'dumi', 'mand', 'vusi', 'phum', 'xola', 'hlong', 'shab',
    'mkhiz', 'ndlov', 'khuma', 'sitho', 'dlami', 'ngcob'
)

AFRIKAANS_NAMES = {
    'armand', 'corne', 'corné', 'jaco', 'willem', 'dirk', 'kobus', 'pieter', 'johan',
    'johannes', 'willem', 'frikkie', 'frik', 'riaan', 'christo', 'schalk', 'carel',
    'bennie', 'francois', 'gert', 'henk', 'koos', 'louw', 'ockert', 'roelof', 'tiaan',
    'wouter', 'andre', 'andré', 'werner', 'joggie', 'stephan', 'marthinus', 'tinus',
    'eben', 'danie', 'daniel', 'herman', 'morne', 'morné', 'gerhard', 'peet', 'ryno',
    'renier', 'dewald', 'deon', 'braam', 'dries', 'andries', 'hendrik', 'ernst', 'eugene',
    'eugéne', 'leon', 'nico', 'anton', 'chris', 'paul', 'alwyn', 'wynand', 'charl',
    'coenraad', 'gustav', 'hennie', 'izak', 'jurie', 'luan', 'mornay', 'sarel', 'theuns',
    'waldo', 'zander', 'annelize', 'annelie', 'elize', 'marilize', 'liezel', 'liezl',
    'sanet', 'ronel', 'rina', 'martie', 'susan', 'wilma', 'hannetjie', 'magda', 'marietjie',
    'daleen', 'alta', 'elmarie', 'yolande', 'charmaine', 'petro', 'estelle', 'lizette',
    'corrie', 'bettie', 'heleen', 'ilse', 'sunette', 'carina', 'lizelle', 
    'andorette', 'natassja', 'mulder', 'botha', 'matthee', 'van der merwe', 'du plessis',
    'venter', 'coetzee', 'fourie', 'pretorius', 'van wyk', 'steyn', 'de jager', 'nel',
    'smit', 'kruger', 'oosthuizen', 'marais', 'erasmus', 'labuschagne', 'oberholzer',
    'potgieter', 'cloete', 'joubert', 'viljoen', 'bezuidenhout', 'le roux', 'meyer',
    'boshoff', 'cronje', 'rossouw', 'swanepoel', 'snyman', 'bester', 'prinsloo',
    'jansen van rensburg', 'engelbrecht', 'van zyl', 'du toit', 'van niekerk', 'grobler',
    'van staden', 'badenhorst',  'myburgh', 'olivier', 'wentzel', 'van heerden',
    'van deventer', 'van rensburg', 'van vuuren', 'van rooyen', 'van jaarsveld', 'van dyk',
    'van biljon', 'van aardt', 'du preez', 'de wet', 'de beer', 'de klerk', 'de villiers',
    'de bruyn', 'de lange', 'de vos', 'de kock', 'naude', 'naudé', 'pienaar', 'theron',
    'strydom', 'swart', 'hattingh', 'basson', 'botes', 'vorster', 'visagie', 'crafford',
    'jooste', 'janse van vuuren', 'van der walt', 'van der westhuizen', 'van der linde',
    'kriel', 'scholtz', 'buys', 'scheepers', 'terblanche', 'brits', 'greyling', 'gous',
    'briel', 'uys', 'roets', 'nortje', 'nortjé', 'senekal', 'gouws', 'blignaut', 'loots',
    'lategan', 'minnaar',
    # Single-token components of compound Afrikaans surnames:
    'merwe', 'plessis', 'toit', 'zyl', 'wyk', 'klerk', 'villiers', 'beer', 'wet',
    'bruyn', 'lange', 'vos', 'kock', 'ruyter', 'heever', 'heerden', 'deventer',
    'rensburg', 'vuuren', 'rooyen', 'jaarsveld', 'dyk', 'biljon', 'aardt', 'walt',
    'westhuizen', 'linde'
}

AFRIKAANS_EXCLUSIVE_WORDS = {
    'baie', 'asseblief', 'dankie', 'goeiedag', 'goeiemore', 'goeiemôre', 'goeienaand',
    'gesels', 'geselsie', 'skakel', 'boodskap', 'vinnige', 'wanneer', 'hoeveel',
    'vandag', 'môre', 'na-ure', 'rustiger', 'onderwys', 'onderwyser', 'onderwyseres',
    'inruil', 'inruilwaarde', 'kwotasie', 'toetsrit', 'voertuig', 'hoor', 'sommer',
    'graag', 'besig', 'hulle', 'julle', 'niemand', 'niks', 'altyd', 'moontlik',
    'seblief', 'luitjie', 'groete', 'lekker', 'saam', 'praat', 'luister', 'kyk',
    'koop', 'verkoop', 'nuwe', 'gebruikte', 'kontak', 'beskikbaar', 'finansiering',
    'deposito', 'aflewer', 'goeie', 'hier', 'ek', 'jy', 'jou', 'sal', 'kan', 'moet',
    'wil', 'nie', 'wat', 'hoe', 'wees', 'lyk', 'program', 'weet', 'voel', 'gou'
}

AFRIKAANS_PHRASES = [
    'ek wil', 'ek is', 'ek het', 'ek sal', 'ek kan', 'ek dink', 'ek hoop', 'ek volg',
    'kan jy', 'kan u', 'sal jy', 'sal u', 'wil jy', 'wil u', 'moet ek', 'moet jy',
    'laat weet', 'hoe gaan', 'baie dankie', 'goeie dag', 'goeie middag', 'goeie more',
    'goeie môre', 'goeie naand', 'as dit', 'as jy', 'wanneer sal', 'vinnige geselsie',
    'vinnige luitjie', 'ek volg op', 'ek wil hoor', 'stuur vir', 'kontak my', 'bel my',
    'skakel my', 'praat met', 'gee my', 'oor whatsapp', 'hoe lyk', 'wat is', 'wat kos',
    'hoeveel kos', 'hoe lyk jou', 'hier van', 'hier weer'
]

ENGLISH_EXCLUSIVE_WORDS = {
    'hello', 'hi', 'thanks', 'thank', 'please', 'regards', 'morning', 'afternoon',
    'evening', 'looking', 'interested', 'quote', 'pricing', 'finance', 'application',
    'delivery', 'deposit', 'quick', 'check', 'checking', 'drive', 'would', 'could',
    'should', 'schedule', 'busy', 'settles', 'assist', 'details', 'vehicle', 'models',
    'particular', 'arrange', 'together', 'whenever', 'ready', 'tomorrow', 'today',
    'suit', 'prefer', 'settle', 'hours', 'chat', 'call', 'speak', 'happy', 'there',
    'search', 'hope', 'having', 'great', 'week', 'chance', 'consider', 'about',
    'english', 'email', 'send', 'message', 'price', 'specs', 'available'
}

ENGLISH_PHRASES = [
    'good day', 'good morning', 'good afternoon', 'good evening',
    'let me know', 'i am', 'i would', 'i will', 'i have', 'can you', 'could you',
    'would you', 'are you', 'do you', 'have you', 'will you', 'when can', 'how are',
    'how is', 'hope you', 'how your', 'your schedule', 'good time', 'quick check',
    'happy to assist', 'give you a call', 'give me a call', 'right here', 'after hours',
    'trade in', 'test drive', 'vehicle search', 'hear from you', 'looking for',
    'here from', 'here again', 'in english', 'please send', 'send me'
]

VEHICLE_KEYWORDS = {
    'brand_vehicle', 'magnite', 'navara', 'qashqai', 'x-trail', 'xtrail', 'almera',
    'suzuki', 'swift', 'jimny', 'baleno', 'fronx', 'brezza', 'ertiga', 'celerio',
    'spresso', 'dzire', 'patrol', 'np200', 'terra', 'micra'
}

def classify_sa_cultural_name(name_str: str) -> str:
    """
    Classifies a customer's name into:
    'african' -> Indigenous South African African name/surname. Default = ENGLISH (Afrikaans forbidden unless customer initiates).
    'afrikaans' -> Traditional Afrikaans name/surname. Default = AFRIKAANS (unless customer responds in English).
    'english_or_other' -> English / International / Unspecified. Default = ENGLISH.
    """
    if not name_str:
        return "english_or_other"

    clean = re.sub(r"\(.*?\)", "", name_str).lower()
    clean = re.sub(r"[/\\-]", " ", clean)
    tokens = [t.strip(" ,.-") for t in clean.split() if t.strip(" ,.-")]
    if not tokens:
        return "english_or_other"

    # Check for African match
    for token in tokens:
        if token in AFRICAN_FIRST_NAMES or token in AFRICAN_SURNAMES:
            return "african"
        if len(token) >= 4 and any(token.startswith(p) for p in AFRICAN_STEM_PREFIXES):
            return "african"

    # Check for Afrikaans compound particles (van der, du plessis, etc.)
    full_str = " ".join(tokens)
    if any(p in full_str for p in ['van der ', 'van den ', 'du plessis', 'du preez', 'du toit', 'de wet', 'de beer', 'de villiers', 'van zyl', 'van niekerk']):
        return "afrikaans"

    for token in tokens:
        if token in AFRIKAANS_NAMES:
            return "afrikaans"

    return "english_or_other"

def score_message_language(text: str) -> tuple[int, int]:
    """Scores a text message for Afrikaans and English markers."""
    if not text or (text.startswith("[") and text.endswith("]")):
        return 0, 0
    t = text.lower()

    afr_score = 0
    eng_score = 0

    # Check phrases
    for p in AFRIKAANS_PHRASES:
        if p in t:
            afr_score += 4
    for p in ENGLISH_PHRASES:
        if p in t:
            eng_score += 4

    # Check exclusive words
    words = re.findall(r"\b\w+\b", t)
    for w in words:
        if w in AFRIKAANS_EXCLUSIVE_WORDS:
            afr_score += 1
        if w in ENGLISH_EXCLUSIVE_WORDS:
            eng_score += 1

    return afr_score, eng_score

def sanitize_dashes(text: str) -> str:
    """Strict Long Dash Ban: Replaces all em/en dashes with standard short hyphens."""
    if not text:
        return text
    return re.sub(r"[\u2014\u2013\u2015]", "-", text)

def enforce_salesperson_identity(text: str) -> str:
    """Enforce SALESPERSON_NAME identity: Replaces CRM username with SALESPERSON_NAME."""
    if not text:
        return text
    if CRM_USERNAME_SHORT:
        if CRM_USERNAME_LAST:
            text = re.sub(rf"\b{re.escape(CRM_USERNAME_SHORT)}\s+{re.escape(CRM_USERNAME_LAST)}\b", SALESPERSON_NAME, text, flags=re.IGNORECASE)
        text = re.sub(rf"\b{re.escape(CRM_USERNAME_SHORT)}\b", SALESPERSON_NAME, text, flags=re.IGNORECASE)
    return text

def mask_phone(phone: str) -> str:
    """Masks phone number for PII compliance (e.g. 072 *** 2838)."""
    p = re.sub(r"[^0-9]", "", str(phone or ""))
    if len(p) >= 9:
        if p.startswith("27") and len(p) == 11:
            return f"0{p[2:4]} *** {p[-4:]}"
        elif p.startswith("0") and len(p) == 10:
            return f"{p[:3]} *** {p[-4:]}"
        return f"{p[:3]} *** {p[-3:]}"
    return "[Masked]"

def query_bridge_api(endpoint: str, method: str = "GET", data: dict = None) -> dict:
    """Queries the JAX WhatsApp Monitor REST API with error handling."""
    url = f"{MONITOR_API_BASE}{endpoint}"
    req = urllib.request.Request(url, method=method)
    req.add_header("Content-Type", "application/json")
    
    body = json.dumps(data).encode("utf-8") if data else None
    try:
        with urllib.request.urlopen(req, data=body, timeout=15) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        err_body = e.read().decode("utf-8") if e.fp else ""
        try:
            return json.loads(err_body)
        except:
            return {"success": False, "error": f"HTTP {e.code}: {e.reason}"}
    except Exception as e:
        return {"success": False, "error": str(e)}

def clean_customer_name(raw_name: str) -> tuple[str, str]:
    """
    Separates a person's real name from attached vehicle models or portal metadata.
    Example: 'Corne Botha / SUV MODEL' -> ('Corne Botha', 'SUV MODEL')
    """
    if not raw_name:
        return "", ""
    
    s = re.sub(r"\(.*?\)", "", raw_name).strip()
    extracted_vehicle = ""
    
    if "/" in s or " - " in s:
        parts = re.split(r"\s*[/\\-]\s*", s)
        if len(parts) >= 2:
            left, right = parts[0].strip(), " ".join(parts[1:]).strip()
            right_lower = right.lower()
            if any(k in right_lower for k in VEHICLE_KEYWORDS):
                extracted_vehicle = right
                s = left
            elif not any(k in left.lower() for k in VEHICLE_KEYWORDS) and len(left.split()) >= 1:
                s = left
    
    clean_digits_only = re.sub(r"[^0-9]", "", s)
    if clean_digits_only and len(clean_digits_only) == len(re.sub(r"[\s+\-()]", "", s)):
        return "", extracted_vehicle
        
    return s.strip(), extracted_vehicle

def clean_first_name(raw_name: str) -> str:
    """
    Extracts customer's first name, with a strict ban on numbers/phone strings.
    Never returns a phone number or string with digits.
    """
    if not raw_name:
        return ""
    
    clean_name, _ = clean_customer_name(raw_name)
    if not clean_name:
        return ""
    
    # Never allow any string containing digits to be a first name
    if re.search(r"\d", clean_name):
        return ""
    
    parts = clean_name.split()
    if not parts:
        return ""
    
    first = parts[0].strip(" ,.-/")
    if len(first) < 2 or re.search(r"\d", first):
        return ""
        
    if first.lower() in ["customer", "client", "lead", "prospect", "dealership", "branch"]:
        return ""
        
    return first.capitalize()

def fetch_phone_from_crm_era(custid: str) -> tuple[str, str]:
    """
    Live fallback: Extracts customer mobile and name directly from CRM ERA
    if the local SQLite record had an empty phone number.
    """
    if not custid:
        return "", ""
    try:
        from bs4 import BeautifulSoup
        from portal_login import get_base_url, login, load_credentials_from_env_file
        
        user, pwd = load_credentials_from_env_file()
        session, res = login(user, pwd)
        url = f'{get_base_url()}/index.cfm?page=pages/customerera_selecttemplate.cfm&custid={custid}'
        r = session.get(url, timeout=15)
        soup = BeautifulSoup(r.text, "html.parser")
        
        mobile_inp = soup.find("input", {"name": "mobile"})
        phone = mobile_inp.get("value", "").strip() if mobile_inp else ""
        forename = soup.find("input", {"name": "forename"})
        surname = soup.find("input", {"name": "surname"})
        fn = forename.get("value", "").strip() if forename else ""
        sn = surname.get("value", "").strip() if surname else ""
        full_name = f"{fn} {sn}".strip()
        
        # Self-heal local SQLite database with newly resolved phone
        if phone and os.path.exists(CRM_DB_PATH):
            try:
                with sqlite3.connect(CRM_DB_PATH) as conn:
                    cur = conn.cursor()
                    cur.execute("UPDATE prospects SET phone = ?, name = COALESCE(NULLIF(?, ''), name), last_updated = CURRENT_TIMESTAMP WHERE custid = ?", (phone, full_name or None, custid))
                    conn.commit()
            except Exception:
                pass
                
        return phone, full_name
    except Exception:
        return "", ""

def tag_whatsapp_prospect(phone: str, name: str):
    """Tags the contact name in the WhatsApp Monitor database so future lookups succeed."""
    if not phone or not name:
        return
    clean_digits = re.sub(r"[^0-9]", "", phone)
    if not clean_digits:
        return
    query_bridge_api("/tag", method="POST", data={
        "phone": clean_digits,
        "name": name,
        "contactType": "prospect"
    })

def resolve_customer_context(query: str, explicit_name: str = "", explicit_phone: str = "") -> dict:
    """
    Resolves customer information from dealership CRM database, live ERA fallback, and WhatsApp Monitor.
    Returns merged profile, history, and language analysis.
    """
    context = {
        "query": query,
        "name": "",
        "first_name": "",
        "phone": "",
        "clean_phone": "",
        "custid": "",
        "vehicle": "",
        "crm_notes": [],
        "whatsapp_messages": [],
        "language_analysis": {
            "detected_language": "english",
            "confidence": "STANDARD",
            "swung_to_afrikaans": False,
            "reasons": []
        }
    }

    if explicit_name:
        cn, ev = clean_customer_name(explicit_name)
        context["name"] = cn
        if ev: context["vehicle"] = ev

    if explicit_phone:
        context["phone"] = explicit_phone

    clean_digits = re.sub(r"[^0-9]", "", query)
    is_phone_query = bool(clean_digits and len(clean_digits) >= 9)

    clean_q, q_veh = clean_customer_name(query)
    if q_veh and not context["vehicle"]:
        context["vehicle"] = q_veh

    # 1. dealership CRM Mirror Lookup
    # Ambiguity is a hard stop; never retry through a first-match fallback.
    crm_info = lookup_customer(CRM_DB_PATH, query)
    if not crm_info and clean_q and clean_q != query:
        crm_info = lookup_customer(CRM_DB_PATH, clean_q)
    if crm_info and explicit_phone and crm_info.get("phone"):
        if normalize_phone(explicit_phone) != normalize_phone(crm_info["phone"]):
            raise ValueError("Explicit recipient does not match the resolved CRM customer")

    if crm_info:
        if not context["custid"]: context["custid"] = crm_info.get("custid", "")
        if not context["name"]:
            cn, ev = clean_customer_name(crm_info.get("name", ""))
            context["name"] = cn
            if ev and not context["vehicle"]: context["vehicle"] = ev
        if not context["phone"]: context["phone"] = crm_info.get("phone", "")
        if not context["vehicle"]: context["vehicle"] = crm_info.get("vehicle", "")

    # 2. Live CRM ERA Phone Fallback (if phone is still empty and custid is known)
    if not context["phone"] and context["custid"]:
        era_phone, era_name = fetch_phone_from_crm_era(context["custid"])
        if era_phone:
            context["phone"] = era_phone
        if era_name and not context["name"]:
            context["name"] = era_name

    # 3. WhatsApp Monitor API /context lookup
    lookup_target = ""
    if context["phone"] or (is_phone_query and clean_digits):
        lookup_target = re.sub(r"[^0-9]", "", context["phone"] or clean_digits)
    elif context["name"]:
        lookup_target = context["name"]
    else:
        lookup_target = query

    encoded_query = urllib.parse.quote(lookup_target)
    api_res = query_bridge_api(f"/context/{encoded_query}")
    
    if api_res.get("success"):
        c = api_res.get("contact", {})
        if not context["name"] and c.get("name"):
            cn, _ = clean_customer_name(c.get("name"))
            if cn: context["name"] = cn
        if not context["phone"] and c.get("phone_number"):
            context["phone"] = c.get("phone_number")
        
        context["whatsapp_messages"] = api_res.get("recent_messages", [])
        if "language_analysis" in api_res:
            context["language_analysis"] = api_res["language_analysis"]
    else:
        raise ValueError("Customer context unavailable or ambiguous; outreach stopped: " + str(api_res.get("error", "bridge lookup failed")))

    # Normalize phone to South African standard (27...)
    raw_p = re.sub(r"[^0-9]", "", context["phone"] or (clean_digits if is_phone_query else ""))
    if raw_p.startswith("0") and len(raw_p) == 10:
        context["clean_phone"] = "27" + raw_p[1:]
    elif raw_p.startswith("27") and len(raw_p) == 11:
        context["clean_phone"] = raw_p
    else:
        context["clean_phone"] = raw_p

    # Verify if customer is registered on WhatsApp via bridge
    context["on_whatsapp"] = True
    if context["clean_phone"]:
        check_res = query_bridge_api(f"/check-number/{context['clean_phone']}")
        if check_res.get("success"):
            context["on_whatsapp"] = bool(check_res.get("exists"))

    # Synchronize resolved name to WhatsApp Monitor database only if on WhatsApp
    if context.get("on_whatsapp", True) and context["clean_phone"] and context["name"]:
        tag_whatsapp_prospect(context["clean_phone"], context["name"])

    # Extract clean first name - strictly banning digits/phone numbers
    context["first_name"] = clean_first_name(context["name"])

    return context

def evaluate_language_preference(context: dict, explicit_language: str = None, intent: str = "") -> dict:
    """
    Bulletproof Multi-Tier South African Automotive Language Decision Engine:
    - Tier 0: Explicit language override from {SALESPERSON_NAME} (--language or explicit 'in Afrikaans'/'in English' directive)
    - Tier 1: Inbound Customer Messages (from_me == False) - Customer's actual words ALWAYS trump cultural assumptions
    - Tier 2: Indigenous South African African Name Guard - African names/surnames STRICTLY default to English;
              Dealership outgoing messages CANNOT swing an African prospect to Afrikaans!
    - Tier 3: Traditional Afrikaans Cultural Name - Established Afrikaans names/surnames default to natural Afrikaans
    - Tier 4: Dealership Outgoing History (Non-African only) - Verified past Afrikaans conversations
    - Tier 5: Universal South African Dealership Default - English
    """
    intent_lower = (intent or "").lower()

    # Tier 0: Explicit Override
    if explicit_language in ["afrikaans", "english"]:
        return {
            "detected_language": explicit_language,
            "confidence": "EXPLICIT",
            "swung_to_afrikaans": explicit_language == "afrikaans",
            "reasons": [f"Explicit language override specified by {SALESPERSON_NAME}: {explicit_language}"]
        }

    if "in afrikaans" in intent_lower or "in afr" in intent_lower:
        return {
            "detected_language": "afrikaans",
            "confidence": "EXPLICIT",
            "swung_to_afrikaans": True,
            "reasons": ["Explicit instruction in intent to message in Afrikaans"]
        }
    if "in english" in intent_lower or "in engels" in intent_lower or "in eng" in intent_lower:
        return {
            "detected_language": "english",
            "confidence": "EXPLICIT",
            "swung_to_afrikaans": False,
            "reasons": ["Explicit instruction in intent to message in English"]
        }

    # Extract customer candidate names
    candidate_names = []
    if context.get("name"):
        candidate_names.append(context["name"])
    if context.get("query") and not re.search(r"\d", context["query"]):
        q = context["query"].strip()
        if not context.get("name") or q.lower() not in context["name"].lower():
            candidate_names.append(q)
    customer_full_name = " ".join(candidate_names).strip()
    cultural_group = classify_sa_cultural_name(customer_full_name)

    messages = context.get("whatsapp_messages", [])

    # Tier 1: Active Inbound Customer Messages (from_me == False)
    customer_afr = 0
    customer_eng = 0
    for msg in messages:
        if not msg.get("from_me"):
            content = msg.get("content") or ""
            ascore, escore = score_message_language(content)
            customer_afr += ascore
            customer_eng += escore

    if customer_afr >= 2 and customer_afr > customer_eng:
        return {
            "detected_language": "afrikaans",
            "confidence": "HIGH",
            "swung_to_afrikaans": True,
            "reasons": [f"Customer actively communicated in Afrikaans (Afr score {customer_afr} vs Eng {customer_eng})"]
        }

    if customer_eng >= 2 and customer_eng > customer_afr:
        return {
            "detected_language": "english",
            "confidence": "HIGH",
            "swung_to_afrikaans": False,
            "reasons": [f"Customer actively communicated in English (Eng score {customer_eng} vs Afr {customer_afr})"]
        }

    # Tier 2: Indigenous South African African Name Guard
    if cultural_group == "african":
        return {
            "detected_language": "english",
            "confidence": "HIGH",
            "swung_to_afrikaans": False,
            "reasons": [
                f"Customer '{customer_full_name}' has an indigenous African cultural name.",
                "Universal South African business standard for African prospects is English.",
                "Afrikaans is strictly prohibited unless the customer personally communicates in Afrikaans first."
            ]
        }

    # Tier 3: Traditional Afrikaans Cultural Name
    if cultural_group == "afrikaans":
        return {
            "detected_language": "afrikaans",
            "confidence": "HIGH",
            "swung_to_afrikaans": True,
            "reasons": [
                f"Customer '{customer_full_name}' has an established Afrikaans cultural name and has not requested English."
            ]
        }

    # Tier 4: Dealership Outgoing Conversation History (ONLY for non-African prospects)
    out_afr = 0
    out_eng = 0
    for msg in messages:
        if msg.get("from_me"):
            content = msg.get("content") or ""
            ascore, escore = score_message_language(content)
            out_afr += ascore
            out_eng += escore

    if out_afr >= 4 and out_afr > out_eng:
        return {
            "detected_language": "afrikaans",
            "confidence": "MEDIUM",
            "swung_to_afrikaans": True,
            "reasons": [f"Prior outgoing conversation was conducted in Afrikaans (Afr score {out_afr} vs Eng {out_eng})"]
        }

    # Tier 5: Universal Dealership Default
    return {
        "detected_language": "english",
        "confidence": "STANDARD",
        "swung_to_afrikaans": False,
        "reasons": ["Universal South African automotive dealership default is English."]
    }

def synthesize_followup_message(first_name: str, vehicle: str, intent: str, language: str) -> str:
    """
    Synthesizes a warm, context-aware 1-2 sentence follow-up in the target language.
    Strictly adheres to:
    - Sender identity: {SALESPERSON_NAME} ({DEALERSHIP_NAME})
    - Strict long dash ban
    - Max 2 sentences, no spec dumping
    - Zero numeric greetings (never 'Hi 082...')
    """
    v_clean = re.sub(r"\(.*?\)", "", vehicle).strip() if vehicle else ""
    intent_lower = (intent or "").lower()

    if language == "afrikaans":
        salutation = f"Hi {first_name}, {SALESPERSON_NAME} hier weer van {DEALERSHIP_NAME} 😊" if first_name else "Goeiedag, {SALESPERSON_NAME} hier weer van {DEALERSHIP_NAME} 😊"
        salutation_new = f"Hi {first_name}, {SALESPERSON_NAME} hier van {DEALERSHIP_NAME} 😊" if first_name else "Goeiedag, {SALESPERSON_NAME} hier van {DEALERSHIP_NAME} 😊"

        # Occupation / Teaching Context
        if any(k in intent_lower for k in ["teacher", "skool", "onderwys", "onderwyser"]):
            return f"{salutation} Ek wil net gou hoor hoe jou dag lyk. Ek weet die skool hou jou seker besig bedags! Sal dit jou pas as ek jou vandag vinnig skakel, of verkies jy dat ons na-ure gesels sodra dinge rustiger is? 🚗"
        elif any(k in intent_lower for k in ["call", "bel", "skakel", "after hours", "na-ure", "schedule", "program"]):
            return f"{salutation} Ek wil net gou hoor hoe jou dag lyk. Sal dit jou pas as ek jou vandag vinnig bel, of verkies jy dat ons na-ure gesels sodra dinge rustiger is? 🚗"
        elif any(k in intent_lower for k in ["trade-in", "inruil"]):
            return f"{salutation_new} Het jy dalk 'n oomblik gehad om na die inruil-besonderhede te kyk, of kan ek jou gou 'n vinnige luitjie gee? 🚗"
        elif any(k in intent_lower for k in ["spec", "quote", "kwotasie", "prys"]):
            return f"{salutation_new} Ek het die opsies gereed vir jou. Sal dit jou pas as ek die syfers hier oor WhatsApp vir jou deurgee? 🚗"
        elif any(k in intent_lower for k in ["test drive", "toetsrit"]):
            return f"{salutation_new} Wil ons vandag of môre 'n vinnige toetsrit reël met die {v_clean or 'voertuig'}? Laat weet my wat jou die beste sal pas! 🚗"
        else:
            return f"{salutation} Ek volg net gou vinnig op om te hoor hoe jou program vandag lyk. Wanneer sal 'n goeie tyd wees vir 'n vinnige geselsie? 🚗"
    else:
        salutation = f"Hi {first_name}, {SALESPERSON_NAME} here again from {DEALERSHIP_NAME} 😊" if first_name else "Good day, {SALESPERSON_NAME} here again from {DEALERSHIP_NAME} 😊"
        salutation_new = f"Hi {first_name}, {SALESPERSON_NAME} here from {DEALERSHIP_NAME} 😊" if first_name else "Good day, {SALESPERSON_NAME} here from {DEALERSHIP_NAME} 😊"

        # Occupation / Teaching Context
        if any(k in intent_lower for k in ["teacher", "school", "teaching"]):
            return f"{salutation} Just doing a quick check-in to see how your schedule looks. I know teaching keeps you busy during the day! Would it suit you if I give you a quick call today, or do you prefer to chat after hours once work settles down? 🚗"
        elif any(k in intent_lower for k in ["call", "after hours", "schedule"]):
            return f"{salutation} Just doing a quick check-in to see how your schedule looks. Would it suit you if I give you a quick call today, or do you prefer to chat after hours once work settles down? 🚗"
        elif any(k in intent_lower for k in ["trade-in", "trade in"]):
            return f"{salutation_new} Did you get a chance to check your current vehicle details, or would you like to arrange a quick evaluation? 🚗"
        elif any(k in intent_lower for k in ["spec", "quote", "pricing"]):
            return f"{salutation_new} I have the details ready for you. Would you like me to send the figures right here on WhatsApp? 🚗"
        elif any(k in intent_lower for k in ["test drive", "testdrive"]):
            return f"{salutation_new} Would you like to set up a quick test drive in the {v_clean or 'vehicle'} this week? Let me know what suits you best! 🚗"
        else:
            return f"{salutation} Just tried giving you a quick call. When would be a good time for a quick chat? 🚗"

def guard_and_adapt_message(message: str, first_name: str, language: str, intent: str, vehicle: str) -> str:
    """
    Validates and guards any draft message text:
    - Enforces {SALESPERSON_NAME} sender identity (replaces {CRM_USERNAME_SHORT})
    - Enforces strict long dash ban
    - Checks language alignment (prevents sending English to an Afrikaans customer)
    - Strictly prevents raw numbers in salutations
    """
    clean_msg = sanitize_dashes(message)
    clean_msg = enforce_salesperson_identity(clean_msg)

    clean_msg = re.sub(r"\b(Hi|Hello|Goeiedag|Good day)\s+\d+[\d\s-]*,\s*", r"\1, ", clean_msg)

    if language == "afrikaans":
        afr_score, eng_score = score_message_language(clean_msg)
        if afr_score < 2 and eng_score >= 2:
            clean_msg = synthesize_followup_message(first_name, vehicle, intent or clean_msg, "afrikaans")
    elif language == "english":
        afr_score, eng_score = score_message_language(clean_msg)
        if afr_score >= 2:
            clean_msg = synthesize_followup_message(first_name, vehicle, intent or clean_msg, "english")

    return clean_msg

def dispatch_whatsapp_message(phone: str, message: str) -> dict:
    """Sends the message via the WhatsApp Monitor bridge with retry."""
    payload = {
        "phone": phone,
        "message": message,
        "authorizedBy": "salesperson_explicit_instruction"
    }

    res = query_bridge_api("/send", method="POST", data=payload)
    if res.get("success"):
        return res

    err = str(res.get("error", ""))
    if "not currently connected" in err or "503" in err or "ECONNREFUSED" in err:
        time.sleep(10)
        res = query_bridge_api("/send", method="POST", data=payload)

    return res

def log_to_portal_crm(custid: str, query: str, note_text: str, days: int = 1) -> dict:
    """Logs the touchpoint note and moves the diary follow-up on CRM."""
    if not action_prospect:
        return {"success": False, "error": "action_prospect module not loaded"}

    clean_note = sanitize_dashes(note_text)
    purpose = "Follow up regarding - - Follow up on WhatsApp reply"

    try:
        res = action_prospect(
            custid=custid or None,
            query=query if not custid else None,
            note=clean_note,
            purpose=purpose,
            days_ahead=days
        )
        return {"success": True, "crm_result": res}
    except Exception as e:
        return {"success": False, "error": str(e)}

def main():
    parser = argparse.ArgumentParser(description="Action Follow-Up & Language Analysis Engine")
    parser.add_argument("--query", "-q", required=True, help="Customer name, surname, or phone number")
    parser.add_argument("--name", "-n", default="", help="Customer full name if known explicitly")
    parser.add_argument("--phone", "-p", default="", help="Customer phone number if known explicitly")
    parser.add_argument("--intent", "-i", default="", help="Intent or instructions from {SALESPERSON_NAME}")
    parser.add_argument("--message", "-m", default="", help="Optional pre-drafted message")
    parser.add_argument("--language", "-l", choices=["afrikaans", "english", "auto"], default="auto", help="Language override")
    parser.add_argument("--days", "-d", type=int, default=1, help="Diary move days ahead (default: 1)")
    parser.add_argument("--dry-run", action="store_true", help="Analyze and draft without sending or logging")
    parser.add_argument("--json", action="store_true", help="Output raw JSON result")

    args = parser.parse_args()

    # Step 1: Resolve customer context
    context = resolve_customer_context(args.query, explicit_name=args.name, explicit_phone=args.phone)

    # Step 2: Language Preference Analysis
    lang_pref = evaluate_language_preference(
        context,
        explicit_language=args.language if args.language != "auto" else None,
        intent=args.intent
    )

    detected_lang = lang_pref["detected_language"]
    first_name = context["first_name"]
    vehicle = context["vehicle"]
    phone = context["clean_phone"]

    if not phone:
        print(f"❌ Error: Could not resolve valid phone number for customer '{args.query}'.", file=sys.stderr)
        sys.exit(1)

    # Step 3: Compose & Guard Message
    if args.message:
        final_message = guard_and_adapt_message(args.message, first_name, detected_lang, args.intent, vehicle)
    else:
        final_message = synthesize_followup_message(first_name, vehicle, args.intent, detected_lang)

    final_message = sanitize_dashes(final_message)
    final_message = re.sub(r"\b(Hi|Hello|Goeiedag|Good day)\s+\d+[\d\s-]*,\s*", r"\1, ", final_message)

    output_data = {
        "customer_name": context["name"] or args.name or args.query,
        "first_name": first_name,
        "phone_masked": mask_phone(phone),
        "phone_raw": phone,
        "custid": context["custid"],
        "vehicle": vehicle,
        "detected_language": detected_lang,
        "confidence": lang_pref["confidence"],
        "swung_to_afrikaans": lang_pref.get("swung_to_afrikaans", False),
        "reasons": lang_pref.get("reasons", []),
        "composed_message": final_message,
        "raw_dispatched_text": final_message,
        "dry_run": args.dry_run
    }

    if args.dry_run:
        if args.json:
            print(json.dumps(output_data, indent=2))
        else:
            print("🔍 DRY RUN - Follow-Up Context & Language Analysis:")
            print(f"👤 Customer: {context['name'] or args.name or args.query} ({mask_phone(phone)})")
            print(f"🗣️ Preferred Language: {detected_lang.upper()} (Confidence: {lang_pref['confidence']})")
            if not context.get("on_whatsapp", True):
                print(f"⚠️ WhatsApp Registration: Customer is NOT registered on WhatsApp!")
            if lang_pref.get("reasons"):
                for r in lang_pref["reasons"]:
                    print(f"   • {r}")
            print(f"\n💬 Composed Message:\n\"{final_message}\"")
        return

    # Step 4: Validate WhatsApp Registration & Dispatch
    if not context.get("on_whatsapp", True):
        cust_name = context["name"] or args.name or args.query
        print(f"❌ Delivery Aborted: {cust_name} ({mask_phone(phone)}) is NOT registered on WhatsApp!")
        print(f"🚫 WhatsApp message was NOT sent.")
        crm_note = f"Follow-up WhatsApp aborted: customer phone ({mask_phone(phone)}) is not registered on WhatsApp. Direct phone call required."
        crm_res = log_to_portal_crm(context["custid"], cust_name, crm_note, days=args.days)
        output_data["crm"] = crm_res
        output_data["dispatch"] = {"success": False, "notOnWhatsApp": True, "error": "Recipient phone number is not registered on WhatsApp"}
        print(f"📅 dealership CRM: Note logged ('Not on WhatsApp - phone call required') and diary moved {args.days} day(s) ahead.")
        if args.json:
            print(json.dumps(output_data, indent=2))
        return

    dispatch_res = dispatch_whatsapp_message(phone, final_message)
    output_data["dispatch"] = dispatch_res

    if not dispatch_res.get("success"):
        if dispatch_res.get("notOnWhatsApp"):
            cust_name = context["name"] or args.name or args.query
            print(f"❌ Delivery Aborted: {cust_name} ({mask_phone(phone)}) is NOT registered on WhatsApp!")
            print(f"🚫 WhatsApp message was NOT sent.")
            crm_note = f"Follow-up WhatsApp aborted: customer phone ({mask_phone(phone)}) is not registered on WhatsApp. Direct phone call required."
            crm_res = log_to_portal_crm(context["custid"], cust_name, crm_note, days=args.days)
            output_data["crm"] = crm_res
            print(f"📅 dealership CRM: Note logged ('Not on WhatsApp - phone call required') and diary moved {args.days} day(s) ahead.")
            if args.json:
                print(json.dumps(output_data, indent=2))
            return
        else:
            print(f"❌ WhatsApp Bridge Dispatch Failed: {dispatch_res.get('error')}", file=sys.stderr)
            if args.json:
                print(json.dumps(output_data, indent=2))
            sys.exit(1)

    # Step 5: Dual-Log to dealership CRM
    crm_note = f"Sent follow-up WhatsApp ({detected_lang.capitalize()}): {final_message[:90]}..."
    crm_res = log_to_portal_crm(context["custid"], context["name"] or args.query, crm_note, days=args.days)
    output_data["crm"] = crm_res

    if args.json:
        print(json.dumps(output_data, indent=2))
    else:
        print(f"✅ Follow-up delivered to {context['name'] or args.query} ({mask_phone(phone)})")
        print(f"🗣️ Language: {detected_lang.upper()} ({lang_pref['confidence']} confidence)")
        for r in lang_pref.get("reasons", []):
            print(f"   • {r}")
        print(f"📲 Delivered WhatsApp Message:")
        print(f"\"{final_message}\"")
        print(f"📅 dealership CRM: Note logged and diary moved {args.days} day(s) ahead.")

if __name__ == "__main__":
    main()
