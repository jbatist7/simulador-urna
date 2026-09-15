#!/usr/bin/env python3
"""
processar_tse.py
Detecta automaticamente o padrão de URL correto do TSE para 2026 
e processa os dados para o Simulador Educacional.
"""

import csv
import json
import zipfile
import requests
import logging
from pathlib import Path
from collections import defaultdict
from datetime import datetime

# ==========================================
# CONFIGURAÇÃO
# ==========================================
ANO_ELEICAO = "2026"
DIR_DADOS = Path("dados")
ARQUIVO_JSON = DIR_DADOS / "dados.json"

UFS = [
    "AC", "AL", "AM", "AP", "BA", "CE", "DF", "ES", "GO", "MA",
    "MT", "MS", "MG", "PA", "PB", "PR", "PE", "PI", "RJ", "RN",
    "RS", "RO", "RR", "SC", "SP", "SE", "TO"
]

MAPEAMENTO_CARGOS = {
    "DEPUTADO FEDERAL": "DEP_FEDERAL",
    "DEPUTADO ESTADUAL": "DEP_ESTADUAL",
    "DEPUTADO DISTRITAL": "DEP_ESTADUAL",
    "SENADOR": "SENADOR",
    "GOVERNADOR": "GOVERNADOR",
    "PRESIDENTE": "PRESIDENTE",
}

SITUACOES_VALIDAS = ["DEFERIDO", "INDEFERIDO COM RECURSO", "CADASTRADO", "AGUARDANDO JULGAMENTO", "APTO", "REGISTRADO", "REGISTRO"]

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)
HEADERS = {"User-Agent": "Mozilla/5.0 (SimuladorEducacional/1.0)"}

# Padrões de URL que o TSE costuma usar (o script vai testar até achar o correto)
PADROES_URL = [
    f"https://cdn.tse.jus.br/estatistica/sead/eleicoes/eleicoes{ANO_ELEICAO}/candidatos{ANO_ELEICAO}/consulta_cand_{ANO_ELEICAO}_{{uf}}.zip",
    f"https://cdn.tse.jus.br/estatistica/sead/eleicoes/eleicoes_{ANO_ELEICAO}/candidatos_{ANO_ELEICAO}/consulta_cand_{ANO_ELEICAO}_{{uf}}.zip",
    f"https://cdn.tse.jus.br/estatistica/sead/eleicoes/eleicoes{ANO_ELEICAO}/consulta_cand_{ANO_ELEICAO}_{{uf}}.zip",
    f"https://cdn.tse.jus.br/estatistica/sead/eleicoes/eleicoes{ANO_ELEICAO}/candidatos/consulta_cand_{ANO_ELEICAO}_{{uf}}.zip"
]

def encontrar_url_valida(uf_teste="SP") -> str | None:
    """Testa os padrões de URL até encontrar um que retorne 200 OK."""
    logger.info("Procurando o padrão de URL correto do TSE...")
    for padrao in PADROES_URL:
        url_teste = padrao.format(uf=uf_teste)
        try:
            logger.info(f"Testando: {url_teste}")
            resposta = requests.head(url_teste, headers=HEADERS, timeout=10, allow_redirects=True)
            if resposta.status_code == 200:
                logger.info(f"✅ Padrão encontrado com sucesso!")
                return padrao
        except Exception as e:
            continue
    
    logger.error("❌ Nenhum padrão de URL funcionou. Verifique se o TSE já liberou os dados.")
    return None

def baixar_arquivo(url: str, destino: Path) -> bool:
    try:
        resposta = requests.get(url, headers=HEADERS, timeout=60, stream=True)
        resposta.raise_for_status()
        with open(destino, 'wb') as f:
            for chunk in resposta.iter_content(chunk_size=8192):
                f.write(chunk)
        return True
    except Exception as e:
        logger.error(f"Erro ao baixar {url}: {e}")
        return False

def extrair_csv(zip_path: Path) -> Path | None:
    try:
        with zipfile.ZipFile(zip_path, 'r') as zip_ref:
            nome_csv = next((f for f in zip_ref.namelist() if f.lower().startswith("consulta_cand") and f.lower().endswith(".csv")), None)
            if not nome_csv: return None
            destino = Path("temp.csv")
            with zip_ref.open(nome_csv) as origem, open(destino, 'wb') as saida:
                saida.write(origem.read())
            return destino
    except Exception as e:
        logger.error(f"Erro ao extrair ZIP: {e}")
        return None

def ler_csv_candidatos(csv_path: Path) -> list[dict]:
    for encoding in ['latin-1', 'iso-8859-1', 'utf-8']:
        try:
            with open(csv_path, 'r', encoding=encoding) as f:
                return list(csv.DictReader(f, delimiter=';'))
        except UnicodeDecodeError:
            continue
    return []

def identificar_titular_e_complementares(chapa: list[dict]) -> dict:
    chapa_ordenada = sorted(chapa, key=lambda x: int(x.get("SQ_CANDIDATO", 0)))
    titular = next((c for c in chapa_ordenada if "VICE" not in c.get("DS_OCUPACAO", "").upper() and "SUPLENT" not in c.get("DS_OCUPACAO", "").upper()), chapa_ordenada[0] if chapa_ordenada else None)
    complementares = [c for c in chapa_ordenada if c != titular]
    return {"titular": titular, "complementares": complementares}

def processar_uf(uf: str, padrao_url: str) -> dict:
    logger.info(f"=== Processando {uf} ===")
    url_zip = padrao_url.format(uf=uf)
    zip_path = Path(f"temp_{uf}.zip")
    
    if not baixar_arquivo(url_zip, zip_path): return {}
    csv_path = extrair_csv(zip_path)
    if not csv_path: return {}
    
    candidatos_raw = ler_csv_candidatos(csv_path)
    zip_path.unlink(missing_ok=True)
    csv_path.unlink(missing_ok=True)
    
    if not candidatos_raw: return {}
    
    candidatos_validos = [c for c in candidatos_raw if c.get("DS_SITUACAO_CANDIDATURA", "").upper() in SITUACOES_VALIDAS]
    logger.info(f"{uf}: {len(candidatos_validos)} candidatos válidos processados.")
    
    agrupados = defaultdict(lambda: defaultdict(list))
    for cand in candidatos_validos:
        cargo_tse = cand.get("DS_CARGO", "").upper()
        if cargo_tse not in MAPEAMENTO_CARGOS: continue
        numero = cand.get("NR_CANDIDATO", "").strip()
        if not numero: continue
        agrupados[cargo_tse][numero].append(cand)
    
    resultado_uf = {}
    for cargo_tse, chapas in agrupados.items():
        cargo_simulador = MAPEAMENTO_CARGOS[cargo_tse]
        
        if cargo_tse == "SENADOR":
            vagas = {"SENADOR_1": [], "SENADOR_2": []}
            for i, (numero, chapa) in enumerate(sorted(chapas.items(), key=lambda x: x[0])):
                dados = identificar_titular_e_complementares(chapa)
                if not dados["titular"]: continue
                registro = {
                    "numero": numero,
                    "nome": dados["titular"].get("NM_URNA_CANDIDATO", ""),
                    "partido": dados["titular"].get("SG_PARTIDO", ""),
                    "suplentes": [s.get("NM_URNA_CANDIDATO", "") for s in dados["complementares"][:2]]
                }
                (vagas["SENADOR_1"] if i % 2 == 0 else vagas["SENADOR_2"]).append(registro)
            resultado_uf["SENADOR_1"] = vagas["SENADOR_1"]
            resultado_uf["SENADOR_2"] = vagas["SENADOR_2"]
        else:
            lista = []
            for numero, chapa in chapas.items():
                dados = identificar_titular_e_complementares(chapa)
                if not dados["titular"]: continue
                registro = {
                    "numero": numero,
                    "nome": dados["titular"].get("NM_URNA_CANDIDATO", ""),
                    "partido": dados["titular"].get("SG_PARTIDO", "")
                }
                if cargo_tse in ["GOVERNADOR", "PRESIDENTE"] and dados["complementares"]:
                    registro["vice"] = dados["complementares"][0].get("NM_URNA_CANDIDATO", "")
                lista.append(registro)
            resultado_uf[cargo_simulador] = lista
            
    return resultado_uf

def main():
    logger.info(f"Iniciando processamento para o ano: {ANO_ELEICAO}")
    
    # 1. Descobrir a URL correta
    padrao_url_correto = encontrar_url_valida("SP")
    if not padrao_url_correto:
        logger.error("Não foi possível encontrar os dados do TSE. Encerrando.")
        return
        
    DIR_DADOS.mkdir(exist_ok=True)
    dados_finais = {}
    
    # 2. Processar todos os estados com a URL correta
    for uf in UFS:
        try:
            dados_uf = processar_uf(uf, padrao_url_correto)
            if dados_uf: dados_finais[uf] = dados_uf
        except Exception as e:
            logger.error(f"Erro em {uf}: {e}")
            
    dados_finais["info"] = {
        "ano": ANO_ELEICAO,
        "tipo": "Eleições Gerais",
        "atualizado_em": datetime.now().isoformat()
    }
    
    with open(ARQUIVO_JSON, 'w', encoding='utf-8') as f:
        json.dump(dados_finais, f, ensure_ascii=False, indent=2)
    
    total = sum(sum(len(v) for v in uf_data.values() if isinstance(v, list)) for uf_data in dados_finais.values() if isinstance(uf_data, dict))
    logger.info(f"✅ Processamento concluído! Total de {total} candidatos registrados no JSON.")

if __name__ == "__main__":
    main()
