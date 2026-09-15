#!/usr/bin/env python3
"""
processar_tse.py
Detecta automaticamente o ano eleitoral mais recente disponível no TSE
e processa os dados para o Simulador Educacional.
"""

import os
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
URL_BASE_TSE = "https://cdn.tse.jus.br/estatistica/sead/eleicoes"
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

SITUACOES_VALIDAS = ["DEFERIDO", "INDEFERIDO COM RECURSO", "CADASTRADO", "AGUARDANDO JULGAMENTO"]

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

HEADERS = {"User-Agent": "Mozilla/5.0 (SimuladorEducacional/1.0)"}


def detectar_ano_mais_recente() -> str:
    """Detecta automaticamente o ano eleitoral mais recente disponível."""
    ano_atual = datetime.now().year
    
    # Tenta anos a partir do atual, descendo
    for ano in range(ano_atual, ano_atual - 10, -1):
        url_teste = f"{URL_BASE_TSE}/eleicoes{ano}/candidatos{ano}/consulta_cand_{ano}_SP.zip"
        try:
            resposta = requests.head(url_teste, headers=HEADERS, timeout=10, allow_redirects=True)
            if resposta.status_code == 200:
                logger.info(f"Ano eleitoral detectado: {ano}")
                return str(ano)
        except:
            continue
    
    # Fallback: retorna o ano atual se não conseguir detectar
    logger.warning("Não foi possível detectar ano. Usando ano atual.")
    return str(ano_atual)


def detectar_tipo_eleicao(ano: str) -> str:
    """Detecta se é eleição geral ou municipal."""
    ano_int = int(ano)
    if ano_int % 4 == 0:
        return "Eleições Gerais"
    else:
        return "Eleições Municipais"


def baixar_arquivo(url: str, destino: Path) -> bool:
    try:
        logger.info(f"Baixando: {url}")
        resposta = requests.get(url, headers=HEADERS, timeout=60, stream=True)
        resposta.raise_for_status()
        with open(destino, 'wb') as f:
            for chunk in resposta.iter_content(chunk_size=8192):
                f.write(chunk)
        return True
    except Exception as e:
        logger.error(f"Erro ao baixar {url}: {e}")
        return False


def extrair_csv(zip_path: Path, uf: str) -> Path | None:
    try:
        with zipfile.ZipFile(zip_path, 'r') as zip_ref:
            nome_csv = None
            for arquivo in zip_ref.namelist():
                if arquivo.lower().startswith("consulta_cand") and arquivo.lower().endswith(".csv"):
                    nome_csv = arquivo
                    break
            
            if not nome_csv:
                return None
            
            destino = Path(f"temp_{uf}.csv")
            with zip_ref.open(nome_csv) as origem, open(destino, 'wb') as saida:
                saida.write(origem.read())
            return destino
    except Exception as e:
        logger.error(f"Erro ao extrair ZIP de {uf}: {e}")
        return None


def ler_csv_candidatos(csv_path: Path) -> list[dict]:
    candidatos = []
    for encoding in ['latin-1', 'iso-8859-1', 'utf-8']:
        try:
            with open(csv_path, 'r', encoding=encoding) as f:
                leitor = csv.DictReader(f, delimiter=';')
                for linha in leitor:
                    candidatos.append(linha)
            return candidatos
        except UnicodeDecodeError:
            continue
    return []


def identificar_titular_e_complementares(chapa: list[dict]) -> dict:
    chapa_ordenada = sorted(chapa, key=lambda x: int(x.get("SQ_CANDIDATO", 0)))
    titular = None
    vices_suplentes = []
    
    for cand in chapa_ordenada:
        ocupacao = cand.get("DS_OCUPACAO", "").upper()
        eh_vice = "VICE" in ocupacao
        eh_suplente = "SUPLENT" in ocupacao
        
        if not eh_vice and not eh_suplente and titular is None:
            titular = cand
        else:
            vices_suplentes.append(cand)
    
    if titular is None and len(chapa_ordenada) > 0:
        titular = chapa_ordenada[0]
        vices_suplentes = chapa_ordenada[1:]
    
    return {"titular": titular, "complementares": vices_suplentes}


def processar_uf(uf: str, ano: str) -> dict:
    logger.info(f"=== Processando {uf} ===")
    
    url_zip = f"{URL_BASE_TSE}/eleicoes{ano}/candidatos{ano}/consulta_cand_{ano}_{uf}.zip"
    zip_path = Path(f"temp_{uf}.zip")
    
    if not baixar_arquivo(url_zip, zip_path):
        return {}
    
    csv_path = extrair_csv(zip_path, uf)
    if not csv_path:
        return {}
    
    candidatos_raw = ler_csv_candidatos(csv_path)
    zip_path.unlink(missing_ok=True)
    csv_path.unlink(missing_ok=True)
    
    if not candidatos_raw:
        return {}
    
    candidatos_validos = [
        c for c in candidatos_raw
        if c.get("DS_SITUACAO_CANDIDATURA", "").upper() in SITUACOES_VALIDAS
    ]
    
    agrupados = defaultdict(lambda: defaultdict(list))
    for cand in candidatos_validos:
        cargo_tse = cand.get("DS_CARGO", "").upper()
        if cargo_tse not in MAPEAMENTO_CARGOS:
            continue
        numero = cand.get("NR_CANDIDATO", "").strip()
        if not numero:
            continue
        agrupados[cargo_tse][numero].append(cand)
    
    resultado_uf = {}
    
    for cargo_tse, chapas in agrupados.items():
        cargo_simulador = MAPEAMENTO_CARGOS[cargo_tse]
        
        if cargo_tse == "SENADOR":
            chapas_ordenadas = sorted(chapas.items(), key=lambda x: x[0])
            vagas = {"SENADOR_1": [], "SENADOR_2": []}
            
            for i, (numero, chapa) in enumerate(chapas_ordenadas):
                dados_chapa = identificar_titular_e_complementares(chapa)
                titular = dados_chapa["titular"]
                if not titular:
                    continue
                
                suplentes = [s.get("NM_URNA_CANDIDATO", "") for s in dados_chapa["complementares"][:2]]
                
                registro = {
                    "numero": numero,
                    "nome": titular.get("NM_URNA_CANDIDATO", ""),
                    "partido": titular.get("SG_PARTIDO", ""),
                    "suplentes": suplentes
                }
                
                if i % 2 == 0:
                    vagas["SENADOR_1"].append(registro)
                else:
                    vagas["SENADOR_2"].append(registro)
            
            resultado_uf["SENADOR_1"] = vagas["SENADOR_1"]
            resultado_uf["SENADOR_2"] = vagas["SENADOR_2"]
        
        else:
            lista_cargos = []
            for numero, chapa in chapas.items():
                dados_chapa = identificar_titular_e_complementares(chapa)
                titular = dados_chapa["titular"]
                if not titular:
                    continue
                
                registro = {
                    "numero": numero,
                    "nome": titular.get("NM_URNA_CANDIDATO", ""),
                    "partido": titular.get("SG_PARTIDO", "")
                }
                
                if cargo_tse in ["GOVERNADOR", "PRESIDENTE"] and dados_chapa["complementares"]:
                    registro["vice"] = dados_chapa["complementares"][0].get("NM_URNA_CANDIDATO", "")
                
                lista_cargos.append(registro)
            
            resultado_uf[cargo_simulador] = lista_cargos
    
    return resultado_uf


def main():
    logger.info("Iniciando processamento de dados do TSE...")
    
    # Detecta automaticamente o ano mais recente
    ano = detectar_ano_mais_recente()
    tipo_eleicao = detectar_tipo_eleicao(ano)
    
    logger.info(f"Processando {tipo_eleicao} de {ano}")
    
    DIR_DADOS.mkdir(exist_ok=True)
    dados_finais = {}
    
    for uf in UFS:
        try:
            dados_uf = processar_uf(uf, ano)
            if dados_uf:
                dados_finais[uf] = dados_uf
        except Exception as e:
            logger.error(f"Erro crítico ao processar {uf}: {e}")
            continue
    
    # Adiciona metadados
    dados_finais["info"] = {
        "ano": ano,
        "tipo": tipo_eleicao,
        "atualizado_em": datetime.now().isoformat()
    }
    
    with open(ARQUIVO_JSON, 'w', encoding='utf-8') as f:
        json.dump(dados_finais, f, ensure_ascii=False, indent=2)
    
    logger.info(f"Arquivo {ARQUIVO_JSON} gerado com sucesso!")


if __name__ == "__main__":
    main()
