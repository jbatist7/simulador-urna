#!/usr/bin/env python3
"""
processar_tse.py
Baixa o arquivo consolidado de candidatos de 2026 do TSE e processa 
os dados para o Simulador Educacional.
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
# URL exata fornecida pelo usuário
URL_ZIP_TSE = "https://cdn.tse.jus.br/estatistica/sead/odsele/consulta_cand/consulta_cand_2026.zip"

DIR_DADOS = Path("dados")
DIR_TEMP = Path("temp_tse")
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

def baixar_arquivo(url: str, destino: Path) -> bool:
    try:
        logger.info(f"Baixando arquivo consolidado do TSE...")
        resposta = requests.get(url, headers=HEADERS, timeout=120, stream=True) # Timeout maior para arquivo grande
        resposta.raise_for_status()
        with open(destino, 'wb') as f:
            for chunk in resposta.iter_content(chunk_size=8192):
                f.write(chunk)
        logger.info("Download concluído com sucesso!")
        return True
    except Exception as e:
        logger.error(f"Erro ao baixar {url}: {e}")
        return False

def ler_todos_csvs_do_zip(zip_path: Path) -> list[dict]:
    """Extrai e lê todos os CSVs de candidatos dentro do ZIP."""
    todos_candidatos = []
    try:
        DIR_TEMP.mkdir(exist_ok=True)
        with zipfile.ZipFile(zip_path, 'r') as zip_ref:
            # Extrai todos os arquivos para a pasta temporária
            zip_ref.extractall(DIR_TEMP)
            
        # Procura por todos os arquivos CSV na pasta temporária
        for csv_file in DIR_TEMP.glob("*.csv"):
            if "consulta_cand" in csv_file.name.lower():
                logger.info(f"Lendo: {csv_file.name}")
                for encoding in ['latin-1', 'iso-8859-1', 'utf-8']:
                    try:
                        with open(csv_file, 'r', encoding=encoding) as f:
                            leitor = csv.DictReader(f, delimiter=';')
                            todos_candidatos.extend(list(leitor))
                        break # Se funcionou com um encoding, sai do loop
                    except UnicodeDecodeError:
                        continue
                        
        # Limpa a pasta temporária
        import shutil
        shutil.rmtree(DIR_TEMP)
        
    except Exception as e:
        logger.error(f"Erro ao processar o ZIP: {e}")
        
    return todos_candidatos

def identificar_titular_e_complementares(chapa: list[dict]) -> dict:
    chapa_ordenada = sorted(chapa, key=lambda x: int(x.get("SQ_CANDIDATO", 0)))
    titular = next((c for c in chapa_ordenada if "VICE" not in c.get("DS_OCUPACAO", "").upper() and "SUPLENT" not in c.get("DS_OCUPACAO", "").upper()), chapa_ordenada[0] if chapa_ordenada else None)
    complementares = [c for c in chapa_ordenada if c != titular]
    return {"titular": titular, "complementares": complementares}

def main():
    logger.info(f"Iniciando processamento para o ano: {ANO_ELEICAO}")
    
    zip_path = Path("consulta_cand_2026.zip")
    
    # 1. Baixar o arquivo único
    if not baixar_arquivo(URL_ZIP_TSE, zip_path):
        logger.error("Falha no download. Encerrando.")
        return
        
    # 2. Ler todos os candidatos de todos os estados
    candidatos_raw = ler_todos_csvs_do_zip(zip_path)
    zip_path.unlink(missing_ok=True) # Apaga o ZIP após extrair
    
    if not candidatos_raw:
        logger.error("Nenhum candidato encontrado no arquivo.")
        return
        
    logger.info(f"Total de linhas brutas lidas: {len(candidatos_raw)}")
    
    # 3. Agrupar por Estado -> Cargo -> Número
    dados_brutos = defaultdict(lambda: defaultdict(lambda: defaultdict(list)))
    
    for cand in candidatos_raw:
        uf = cand.get("SG_UF", "").strip().upper()
        if uf not in UFS:
            continue
            
        cargo_tse = cand.get("DS_CARGO", "").upper()
        if cargo_tse not in MAPEAMENTO_CARGOS:
            continue
            
        situacao = cand.get("DS_SITUACAO_CANDIDATURA", "").upper()
        if situacao not in SITUACOES_VALIDAS:
            continue
            
        numero = cand.get("NR_CANDIDATO", "").strip()
        if not numero:
            continue
            
        dados_brutos[uf][cargo_tse][numero].append(cand)
        
    # 4. Processar e formatar para o JSON final
    dados_finais = {}
    
    for uf in UFS:
        if uf not in dados_brutos:
            continue
            
        resultado_uf = {}
        for cargo_tse, numeros in dados_brutos[uf].items():
            cargo_simulador = MAPEAMENTO_CARGOS[cargo_tse]
            
            if cargo_tse == "SENADOR":
                vagas = {"SENADOR_1": [], "SENADOR_2": []}
                for i, (numero, chapa) in enumerate(sorted(numeros.items(), key=lambda x: x[0])):
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
                for numero, chapa in numeros.items():
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
                
        dados_finais[uf] = resultado_uf

    # 5. Salvar o JSON
    dados_finais["info"] = {
        "ano": ANO_ELEICAO,
        "tipo": "Eleições Gerais",
        "atualizado_em": datetime.now().isoformat()
    }
    
    DIR_DADOS.mkdir(exist_ok=True)
    with open(ARQUIVO_JSON, 'w', encoding='utf-8') as f:
        json.dump(dados_finais, f, ensure_ascii=False, indent=2)
        
    total_candidatos = sum(
        sum(len(v) for v in uf_data.values() if isinstance(v, list)) 
        for uf_data in dados_finais.values() if isinstance(uf_data, dict)
    )
    logger.info(f"✅ Processamento concluído! {total_candidatos} candidatos salvos em dados.json.")

if __name__ == "__main__":
    main()
