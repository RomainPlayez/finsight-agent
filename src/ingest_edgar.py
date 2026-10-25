"""
ingest_edgar.py permet de recuperer les donnees financieres standardisees (XBRL)
pour une liste de tickers depuis l'API "companyfacts" de SEC EDGAR, et les
charge dans la base SQLite.
Lancement (depuis la racine du projet) : python -m src.ingest_edgar
"""

import os
import time
from datetime import date

import requests
from dotenv import load_dotenv

from src.db import get_connection, init_db, upsert_company, upsert_financial

load_dotenv()

USER_AGENT = os.getenv("SEC_EDGAR_USER_AGENT", "FinSight-Agent contact@example.com")
HEADERS = {"User-Agent": USER_AGENT}

TICKERS = ["AAPL", "MSFT", "AMZN", "JPM", "GS", "NVDA", "TSLA", "META"]

# Pour chaque metrique voulue, plusieurs tags US-GAAP candidats
# (toutes les entreprises n'utilisent pas exactement le meme tag XBRL). Il convient en fonction de l'entreprise à ajouter, d'ajouter des valeurs à cette liste le tag n'est pas déjà dedans
METRIC_TAGS = {
    "Revenues": [
        "Revenues",
        "RevenueFromContractWithCustomerExcludingAssessedTax",
        "SalesRevenueNet",
        "RevenuesNetOfInterestExpense",
    ],
    "NetIncomeLoss": ["NetIncomeLoss"],
    "Assets": ["Assets"],
    "Liabilities": ["Liabilities"],
    "OperatingCashFlow": [
        "NetCashProvidedByUsedInOperatingActivities",
        "NetCashProvidedByUsedInOperatingActivitiesContinuingOperations",
    ],
}

MIN_FISCAL_YEAR = 2021


def fetch_ticker_to_cik() -> dict:
    "Recupere le mapping ticker -> CIK (10 chiffres, zeros de tete)"
    url = "https://www.sec.gov/files/company_tickers.json"
    resp = requests.get(url, headers=HEADERS, timeout=30)
    resp.raise_for_status()
    data = resp.json()
    mapping = {}
    for entry in data.values():
        ticker = entry["ticker"].upper()
        cik10 = str(entry["cik_str"]).zfill(10)
        mapping[ticker] = cik10
    return mapping


def fetch_company_facts(cik10: str) -> dict:
    url = f"https://data.sec.gov/api/xbrl/companyfacts/CIK{cik10}.json"
    resp = requests.get(url, headers=HEADERS, timeout=30)
    resp.raise_for_status()
    return resp.json()


def extract_annual_values(facts: dict, tag_candidates: list) -> dict:
    """
    Pour une liste de tags XBRL candidats, retourne {annee_fiscale: valeur}
    en ne gardant que les faits annuels.

    Attention : (piege XBRL reel, decouvert sur les donnees Microsoft) : le
    champ "fy" fourni par la SEC designe l'annee fiscale DU DEPOT (le
    10-K dans lequel la donnee apparait), Ppas la periode qu'elle couvre.
    Un 10-K republie systematiquement 2-3 annees de comparatif pour
    chaque poste du compte de resultat / bilan, et la SEC tague TOUTES
    ces valeurs avec le meme "fy" (celui du depot), avec la meme date de
    depot ("filed"). Se fier a "fy" seul revient donc, selon l'ordre
    d'apparition dans la reponse de l'API, a recuperer une donnee vieille
    de 1 ou 2 ans au lieu de la bonne annee.

    On determine donc la vraie annee fiscale a partir de la date de fin
    de periode ("end"), la convention standard (l'annee fiscale porte le
    nom de l'annee calendaire ou elle se termine). Pour les faits de
    duree (revenus, resultat net, cash flow), on exclut aussi les faits
    trimestriels qui partagent le meme marquage form=10-K / fp=FY dans
    les donnees SEC (presents car un 10-K peut inclure des tableaux
    trimestriels en annexe) en ne gardant que les durees proches d'un an.
    """
    us_gaap = facts.get("facts", {}).get("us-gaap", {})
    best_by_year = {}

    for tag in tag_candidates:
        tag_data = us_gaap.get(tag)
        if not tag_data:
            continue
        usd_facts = tag_data.get("units", {}).get("USD", [])
        for item in usd_facts:
            if item.get("form") != "10-K" or item.get("fp") != "FY":
                continue
            end_str = item.get("end")
            val = item.get("val")
            filed = item.get("filed")
            if not end_str or val is None or not filed:
                continue

            end = date.fromisoformat(end_str)

            start_str = item.get("start")
            if start_str:
                start = date.fromisoformat(start_str)
                duration_days = (end - start).days
                if duration_days < 300:
                    continue  # fait trimestriel, pas annuel

            fiscal_year = end.year
            if fiscal_year < MIN_FISCAL_YEAR:
                continue

            current = best_by_year.get(fiscal_year)
            if current is None or filed > current["filed"]:
                best_by_year[fiscal_year] = {"val": val, "filed": filed}

    return {fy: v["val"] for fy, v in best_by_year.items()}


def extract_liabilities_fallback(facts: dict, existing_years: set) -> dict:
    """
    Certaines entreprises (ex. Amazon) ne taguent pas 'Liabilities'
    directement, seulement 'LiabilitiesAndStockholdersEquity' (total du
    bilan) et 'StockholdersEquity' (capitaux propres). On calcule alors :
        Liabilities = LiabilitiesAndStockholdersEquity - StockholdersEquity
    uniquement pour les annees non deja couvertes par le tag direct.
    """
    total_years = extract_annual_values(facts, ["LiabilitiesAndStockholdersEquity"])
    equity_years = extract_annual_values(facts, ["StockholdersEquity"])
    computed = {}
    for fy, total in total_years.items():
        if fy in existing_years:
            continue
        equity = equity_years.get(fy)
        if equity is not None:
            computed[fy] = total - equity
    return computed


def main():
    conn = get_connection()
    init_db(conn)

    print("Recuperation du mapping ticker -> CIK...")
    ticker_to_cik = fetch_ticker_to_cik()

    for ticker in TICKERS:
        cik10 = ticker_to_cik.get(ticker)
        if not cik10:
            print(f"[!] Ticker introuvable : {ticker}")
            continue

        print(f"\n=== {ticker} (CIK {cik10}) ===")
        facts = fetch_company_facts(cik10)
        company_name = facts.get("entityName", ticker)
        company_id = upsert_company(conn, cik=cik10, ticker=ticker, name=company_name)

        for metric_name, tag_candidates in METRIC_TAGS.items():
            values_by_year = extract_annual_values(facts, tag_candidates)

            if metric_name == "Liabilities":
                fallback = extract_liabilities_fallback(facts, set(values_by_year.keys()))
                if fallback:
                    print(f"  (Liabilities calcule pour {sorted(fallback.keys())} via L&SE - Equity)")
                values_by_year.update(fallback)

            for fy, val in sorted(values_by_year.items()):
                upsert_financial(conn, company_id, fy, metric_name, float(val))
            print(
                f"  {metric_name}: {len(values_by_year)} annees "
                f"({sorted(values_by_year.keys())})"
            )

        time.sleep(0.3)

    print("\nIngestion terminee.")


if __name__ == "__main__":
    main()
