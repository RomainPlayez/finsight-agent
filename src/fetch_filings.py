"""
fetch_filings.py : pour chaque entreprise, retrouve son 10-K le plus
recent sur EDGAR et en extrait la section MD&A (Item 7 : "Management's
Discussion and Analysis"), la partie la plus riche en explications
qualitatives sur la performance financiere de la structure .
"""

import os
import re
import requests
from bs4 import BeautifulSoup
from dotenv import load_dotenv

load_dotenv()

USER_AGENT = os.getenv("SEC_EDGAR_USER_AGENT", "FinSight-Agent contact@example.com")
HEADERS = {"User-Agent": USER_AGENT}


def get_latest_10k_url(cik10: str) -> str | None:
    "Retourne l'URL du document principal du 10-K le plus recent."
    url = f"https://data.sec.gov/submissions/CIK{cik10}.json"
    resp = requests.get(url, headers=HEADERS, timeout=30)
    resp.raise_for_status()
    data = resp.json()

    recent = data["filings"]["recent"]
    for i, form in enumerate(recent["form"]):
        if form == "10-K":
            accession = recent["accessionNumber"][i].replace("-", "")
            primary_doc = recent["primaryDocument"][i]
            cik_int = int(cik10)
            return f"https://www.sec.gov/Archives/edgar/data/{cik_int}/{accession}/{primary_doc}"
    return None


def fetch_mdna_text(filing_url: str, max_chars: int = 40000) -> str:
    """
    Telecharge le 10-K et extrait le texte de la section MD&A (Item 7 :
    "Management's Discussion and Analysis"). Approche heuristique par
    recherche de texte

    Deux signaux, du plus fiable au plus generique :

    1. La phrase d'ouverture standard du MD&A ("The following is
       Management's discussion and analysis..."), un boilerplate quasi
       universel dans les 10-K qui n'apparait JAMAIS dans une table des
       matieres ni dans un simple renvoi, seulement au vrai debut du
       contenu. Indispensable pour certaines grandes banques (ex.
       JPMorgan) dont le "Item 7." du corps du document n'est qu'un
       paragraphe de renvoi d'une ligne vers le vrai contenu, place plus
       loin sous un titre non numerote ("Management's discussion and
       analysis"), une recherche basee sur "Item 7." ne trouve alors
       que des renvois courts, jamais le vrai contenu.

    2. A defaut (entreprise sans ce boilerplate), on repere toutes les
       occurrences de "Item 7." et on garde celle qui produit le plus
       long segment jusqu'au marqueur de fin suivant ("Item 7A."/
       "Item 8."), une entree de table des matieres donne toujours un
       tres court segment, contrairement au vrai contenu.
    """
    resp = requests.get(filing_url, headers=HEADERS, timeout=30)
    resp.raise_for_status()

    soup = BeautifulSoup(resp.content, "html.parser")
    full_text = soup.get_text(separator="\n")
    full_text = re.sub(r"\n{2,}", "\n", full_text)

    intro_match = re.search(
        r"the following is management.{0,2}s discussion and analysis"
        r"|the following discussion and analysis",
        full_text,
        re.IGNORECASE,
    )

    if intro_match:
        best_start = intro_match.start()
    else:
        start_matches = list(re.finditer(r"item\s+7\.?\s+management", full_text, re.IGNORECASE))
        if not start_matches:
            start_matches = list(re.finditer(r"item\s+7\.", full_text, re.IGNORECASE))
        if not start_matches:
            return ""  # section non trouvee -- a signaler

        def segment_length(start: int) -> int:
            end_match = re.search(r"item\s+7a\.|item\s+8\.", full_text[start + 10:], re.IGNORECASE)
            return end_match.start() if end_match else max_chars

        best_start = max(start_matches, key=lambda m: segment_length(m.start())).start()

    end_match = re.search(r"item\s+7a\.|item\s+8\.", full_text[best_start + 10:], re.IGNORECASE)
    end = best_start + 10 + end_match.start() if end_match else best_start + max_chars

    return full_text[best_start:end].strip()[:max_chars]
