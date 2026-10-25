"""
agent.py : agent conversationnel avec appel d'outils (function calling)
qui orchestre deux sources d'information sur les 8 entreprises couvertes
par le projet:
1. un outil SQL (get_financial_metric) pour des chiffres exacts issus des
donnees XBRL structurees (SEC EDGAR)
2. un outil de recherche semantique (search_10k_narrative) sur la section
MD&A du dernier 10-K, pour le contexte qualitatif
"""

import json
import os
from dotenv import load_dotenv
from openai import OpenAI
from src.db import get_connection, get_metric
from src.rag import rag_search

load_dotenv()

MODEL = "gpt-5-mini"

COMPANIES = ["AAPL", "MSFT", "AMZN", "JPM", "GS", "NVDA", "TSLA", "META"]
METRICS = ["Revenues", "NetIncomeLoss", "Assets", "Liabilities", "OperatingCashFlow"]

SYSTEM_PROMPT = f"""Tu es un assistant d'analyse financiere specialise sur 8 entreprises cotees : {", ".join(COMPANIES)}.

Tu as acces a deux outils :

1. get_financial_metric : donnees chiffrees exactes (revenus, resultat net, actifs, passifs, flux de tresorerie operationnel) extraites des 10-K deposes aupres de la SEC. Utilise-le pour toute question portant sur un chiffre precis.

2. search_10k_narrative : recherche semantique dans la section "Management's Discussion and Analysis" (MD&A) du dernier 10-K de chaque entreprise. Utilise-le pour les questions qualitatives ("pourquoi", "comment", "quels facteurs...").

Regles importantes :
- Ne devine et n'invente JAMAIS un chiffre : si get_financial_metric ne retourne rien pour une entreprise/annee/metrique donnee, dis clairement que la donnee n'est pas disponible plutot que de l'estimer.
- Pour une question qui combine chiffres et explication (ex. "pourquoi le CA a-t-il augmente"), utilise d'abord get_financial_metric puis search_10k_narrative pour contextualiser.
- Cite toujours l'entreprise et l'annee fiscale associees aux chiffres que tu donnes.
- Si la question porte sur une entreprise hors de la liste couverte, dis-le clairement au lieu de repondre depuis tes connaissances generales.
- Reponds en francais, de maniere concise et factuelle.
"""

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "get_financial_metric",
            "description": (
                "Retourne la valeur exacte (en USD) d'une metrique financiere "
                "pour une entreprise et une annee fiscale donnees, issue des "
                "donnees XBRL structurees des 10-K SEC EDGAR."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "ticker": {
                        "type": "string",
                        "enum": COMPANIES,
                        "description": "Symbole boursier de l'entreprise.",
                    },
                    "fiscal_year": {
                        "type": "integer",
                        "description": "Annee fiscale (ex. 2024).",
                    },
                    "metric": {
                        "type": "string",
                        "enum": METRICS,
                        "description": "Metrique financiere demandee.",
                    },
                },
                "required": ["ticker", "fiscal_year", "metric"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_10k_narrative",
            "description": (
                "Recherche semantique dans la section MD&A (analyse qualitative "
                "de la direction) du dernier 10-K d'une ou plusieurs entreprises. "
                "Utile pour comprendre le contexte et les raisons derriere les chiffres."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": (
                            "Sujet a rechercher, de preference en anglais "
                            "(les documents sources sont en anglais)."
                        ),
                    },
                    "ticker": {
                        "type": "string",
                        "enum": COMPANIES,
                        "description": "Limiter la recherche a cette entreprise (optionnel).",
                    },
                    "top_k": {
                        "type": "integer",
                        "description": "Nombre d'extraits a retourner (par defaut 5).",
                    },
                },
                "required": ["query"],
            },
        },
    },
]

_client = OpenAI()


def _call_get_financial_metric(ticker: str, fiscal_year: int, metric: str) -> str:
    conn = get_connection()
    row = get_metric(conn, ticker, fiscal_year, metric)
    conn.close()
    if row is None:
        return json.dumps({
            "found": False,
            "message": f"Aucune donnee pour {ticker} / annee fiscale {fiscal_year} / {metric}.",
        })
    value, unit = row
    return json.dumps({
        "found": True,
        "ticker": ticker.upper(),
        "fiscal_year": fiscal_year,
        "metric": metric,
        "value": value,
        "unit": unit,
    })


def _call_search_10k_narrative(query: str, ticker: str | None = None, top_k: int = 5) -> str:
    results = rag_search(query, ticker=ticker, top_k=top_k)
    return json.dumps([
        {"ticker": r["ticker"], "text": r["text"], "distance": r["distance"]}
        for r in results
    ])


TOOL_FUNCTIONS = {
    "get_financial_metric": _call_get_financial_metric,
    "search_10k_narrative": _call_search_10k_narrative,
}


def run_agent(user_message: str, history: list | None = None) -> tuple[str, list]:
    """
    Envoie un message utilisateur a l'agent, execute les eventuels appels
    d'outils demandes par le modele (potentiellement plusieurs allers-retours),
    et retourne (reponse_texte, historique_mis_a_jour).

    'history' est la liste des messages precedents (hors system prompt)
    passe None pour demarrer une nouvelle conversation. L'historique retourne
    est a repasser tel quel au prochain appel pour garder le contexte.
    """
    messages = [{"role": "system", "content": SYSTEM_PROMPT}]
    if history:
        messages.extend(history)
    messages.append({"role": "user", "content": user_message})

    while True:
        response = _client.chat.completions.create(
            model=MODEL,
            messages=messages,
            tools=TOOLS,
        )
        message = response.choices[0].message

        if not message.tool_calls:
            messages.append({"role": "assistant", "content": message.content})
            return message.content, messages[1:]  # on renvoie l'historique sans le system prompt

        # Le modele demande un ou plusieurs outils : on les execute tous,
        # on ajoute les resultats a la conversation, puis on redemande une
        # reponse au modele (boucle) -- il peut enchainer plusieurs outils
        # avant de repondre (ex. get_financial_metric puis search_10k_narrative).
        messages.append({
            "role": "assistant",
            "content": message.content,
            "tool_calls": [tc.model_dump() for tc in message.tool_calls],
        })
        for tool_call in message.tool_calls:
            fn_name = tool_call.function.name
            fn_args = json.loads(tool_call.function.arguments)
            fn = TOOL_FUNCTIONS.get(fn_name)
            if fn is None:
                result = json.dumps({"error": f"Outil inconnu : {fn_name}"})
            else:
                result = fn(**fn_args)
            messages.append({
                "role": "tool",
                "tool_call_id": tool_call.id,
                "content": result,
            })