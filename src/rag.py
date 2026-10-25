"""
rag.py : construit et interroge un index vectoriel (Chroma) sur les
sections MD&A des 10-K de chaque entreprise, pour permettre des reponses
de type (pourquoi/comment) en plus des donnees chiffrees de la base SQL.

Construire l'index (a lancer une fois, ou apres mise a jour des donnees) :
    python -m src.rag

Ensuite, rag_search(...) peut etre appelee depuis l'agent.
"""

import os
from pathlib import Path

import chromadb
from dotenv import load_dotenv
from openai import OpenAI

from src.fetch_filings import get_latest_10k_url, fetch_mdna_text

load_dotenv()

CHROMA_PATH = Path(__file__).resolve().parent.parent / "data" / "chroma_db"
COLLECTION_NAME = "mdna_sections"
EMBEDDING_MODEL = "text-embedding-3-small"

TICKER_TO_CIK = {
    "AAPL": "0000320193",
    "MSFT": "0000789019",
    "AMZN": "0001018724",
    "JPM": "0000019617",
    "GS": "0000886982",
    "NVDA": "0001045810",
    "TSLA": "0001318605",
    "META": "0001326801",
}

_openai_client = OpenAI()  # utilise OPENAI_API_KEY depuis l'environnement


def chunk_text(text: str, chunk_size: int = 1200, overlap: int = 200) -> list[str]:
    """
    Decoupe le texte en morceaux de chunk_size caracteres, avec un
    chevauchement (overlap) entre morceaux consecutifs pour eviter de
    couper une idee importante.
    """
    chunks = []
    start = 0
    while start < len(text):
        end = start + chunk_size
        chunks.append(text[start:end])
        start = end - overlap
    return [c.strip() for c in chunks if len(c.strip()) > 50]


def embed_texts(texts: list[str]) -> list[list[float]]:
    response = _openai_client.embeddings.create(model=EMBEDDING_MODEL, input=texts)
    return [item.embedding for item in response.data]


def get_collection():
    client = chromadb.PersistentClient(path=str(CHROMA_PATH))
    return client.get_or_create_collection(COLLECTION_NAME)


def build_index():
    collection = get_collection()

    for ticker, cik10 in TICKER_TO_CIK.items():
        print(f"\n=== {ticker} ===")
        filing_url = get_latest_10k_url(cik10)
        if not filing_url:
            print("  [!] Aucun 10-K trouve.")
            continue
        print(f"  10-K : {filing_url}")

        mdna_text = fetch_mdna_text(filing_url)
        if not mdna_text:
            print("  [!] Section MD&A non trouvee (a inspecter manuellement).")
            continue
        print(f"  MD&A extraite : {len(mdna_text)} caracteres")

        chunks = chunk_text(mdna_text)
        print(f"  {len(chunks)} chunks")

        embeddings = embed_texts(chunks)
        ids = [f"{ticker}_{i}" for i in range(len(chunks))]
        metadatas = [{"ticker": ticker, "chunk_index": i} for i in range(len(chunks))]

        collection.upsert(
            ids=ids,
            embeddings=embeddings,
            documents=chunks,
            metadatas=metadatas,
        )
        print(f"  Indexe dans Chroma.")

    print("\nIndex RAG construit.")


def rag_search(query: str, ticker: str | None = None, top_k: int = 5) -> list[dict]:
    """
    Recherche semantique : retourne les top_k passages de MD&A les plus
    pertinents pour la question posee, filtres sur une entreprise si
    ticker est precise.
    """
    collection = get_collection()
    query_embedding = embed_texts([query])[0]

    where = {"ticker": ticker.upper()} if ticker else None
    results = collection.query(
        query_embeddings=[query_embedding],
        n_results=top_k,
        where=where,
    )

    passages = []
    for doc, meta, dist in zip(
        results["documents"][0], results["metadatas"][0], results["distances"][0]
    ):
        passages.append({"text": doc, "ticker": meta["ticker"], "distance": dist})
    return passages


if __name__ == "__main__":
    build_index()