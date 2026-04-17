#!/usr/bin/env python3
"""
Fase 0: Exploración NLP de tickets históricos.
Genera config/conceptos.json con taxonomía de señales.

Uso:
    python fase0_explorar.py --days 30
    python fase0_explorar.py --days 7 --output config/conceptos_test.json
"""
import argparse
import json
import os
import re
from collections import Counter
from pathlib import Path

import spacy
from dotenv import load_dotenv

from zendesk_client import ZendeskClient
from storage import Storage

load_dotenv()

# Señales de partida (semillas conocidas — se enriquecen con NLP)
SEMILLAS_TECNICO = [
    "error", "no funciona", "no puedo", "fallo", "bug", "problema técnico",
    "cobrado dos veces", "doble cobro", "no carga", "página en blanco",
    "error 500", "no me deja", "no responde", "sigue cobrando"
]
SEMILLAS_NO_TECNICO = [
    "quiero darme de baja", "solicito baja", "cambiar dirección",
    "actualizar datos", "información sobre", "cuánto cuesta",
    "factura del mes", "cambio de cuenta bancaria"
]
SISTEMAS_SEMILLA = {
    "stripe": ["stripe", "tarjeta", "visa", "mastercard", "cobro tarjeta"],
    "paypal": ["paypal", "pay pal"],
    "sepa_iban": ["iban", "sepa", "domiciliación", "domiciliacion", "cuenta bancaria", "recibo bancario"],
    "auth_login": ["login", "contraseña", "no puedo entrar", "acceso", "sesión", "sesion"],
    "crm_frontend": ["página", "pagina", "botón", "boton", "formulario", "no carga", "pantalla"],
}
TIPOS_SEMILLA = {
    "cobro_indebido": ["cobrado dos veces", "doble cobro", "cobro duplicado", "cargo no autorizado"],
    "baja_no_procesada": ["di de baja", "sigo siendo cobrado", "cancelé", "baja no efectiva", "no tramitaron"],
    "error_acceso": ["no puedo entrar", "contraseña no funciona", "error al iniciar"],
    "error_interfaz": ["no carga", "error 500", "página en blanco", "no responde"],
}

def limpiar_texto(texto: str) -> str:
    """Elimina saludos comunes, URLs, firmas y HTML."""
    texto = re.sub(r"<[^>]+>", " ", texto)
    texto = re.sub(r"http\S+", "", texto)
    texto = re.sub(r"(hola|buenos días|buenas tardes|estimad[oa]s?|saludos|un saludo|atentamente)[,\s]*", "", texto, flags=re.IGNORECASE)
    texto = re.sub(r"\s+", " ", texto).strip()
    return texto[:2000]

def extraer_keywords_nlp(textos: list[str], nlp) -> dict:
    """Extrae keywords relevantes usando spaCy."""
    contador = Counter()
    for texto in textos:
        doc = nlp(texto[:1000])
        for token in doc:
            if (token.pos_ in ("NOUN", "VERB", "ADJ")
                    and not token.is_stop
                    and len(token.lemma_) > 3):
                contador[token.lemma_.lower()] += 1
    return dict(contador.most_common(100))

def calcular_coocurrencias(textos: list[str], top_terms: list[str]) -> dict:
    """Calcula co-ocurrencias entre top_terms dentro de una ventana."""
    cooc = Counter()
    for texto in textos:
        texto_lower = texto.lower()
        presentes = [t for t in top_terms if t in texto_lower]
        for i, t1 in enumerate(presentes):
            for t2 in presentes[i+1:]:
                pair = tuple(sorted([t1, t2]))
                cooc[pair] += 1
    return {f"{a}+{b}": c for (a, b), c in cooc.most_common(30)}

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--days", type=int, default=30)
    default_output = str(Path(os.environ.get("CONFIG_DIR", "./config")) / "conceptos.json")
    parser.add_argument("--output", default=default_output)
    args = parser.parse_args()

    print(f"📥 Descargando tickets de los últimos {args.days} días...")
    client = ZendeskClient()
    tickets = client.get_tickets(days_back=args.days)
    print(f"   → {len(tickets)} tickets descargados")

    print("🔍 Procesando con spaCy...")
    nlp = spacy.load("es_core_news_lg")
    textos = [limpiar_texto(f"{t['subject']} {t['body_preview']}") for t in tickets]
    keywords_freq = extraer_keywords_nlp(textos, nlp)
    top_terms = list(keywords_freq.keys())[:50]
    coocurrencias = calcular_coocurrencias(textos, top_terms)

    conceptos = {
        "version": "1.0",
        "generated_at": __import__("datetime").datetime.utcnow().isoformat(),
        "muestra_tickets": len(tickets),
        "filtrado_tecnico": {
            "indicadores_tecnico": SEMILLAS_TECNICO,
            "indicadores_no_tecnico": SEMILLAS_NO_TECNICO,
            "umbral_confianza_ollama": 0.65,
        },
        "sistemas": {k: {"keywords": v, "descripcion": k} for k, v in SISTEMAS_SEMILLA.items()},
        "tipos_problema": {
            k: {"keywords": v, "severidad_default": "HIGH" if "cobro" in k or "baja" in k else "MEDIUM"}
            for k, v in TIPOS_SEMILLA.items()
        },
        "keywords_frecuentes": keywords_freq,
        "coocurrencias_top": coocurrencias,
        "umbral_ancla_directa": 2,
        "conceptos_descubiertos": [],
    }

    Path(args.output).parent.mkdir(parents=True, exist_ok=True)
    with open(args.output, "w") as f:
        json.dump(conceptos, f, ensure_ascii=False, indent=2)

    print(f"\n✅ conceptos.json generado: {args.output}")
    print(f"   Top 10 keywords: {top_terms[:10]}")
    print(f"   Co-ocurrencias: {list(coocurrencias.items())[:5]}")
    print("\n⚠️  Revisa y ajusta conceptos.json manualmente antes de ejecutar el pipeline.")

if __name__ == "__main__":
    main()
