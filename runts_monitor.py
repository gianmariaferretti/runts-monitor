import requests
import json
import os
import pandas as pd
from bs4 import BeautifulSoup
from datetime import datetime
import logging
import time
import sys
import re

# Configurazione del logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger(__name__)

# Percorsi dei file
CONFIG_FILE = 'config.json'
DATA_DIR = 'data'
HISTORY_FILE = f"{DATA_DIR}/history.json"

# Assicuriamoci che la directory data esista
if not os.path.exists(DATA_DIR):
    os.makedirs(DATA_DIR)

def load_config():
    """Carica la configurazione dal file config.json"""
    with open(CONFIG_FILE, 'r') as f:
        return json.load(f)

def load_history():
    """Carica lo storico dei dati degli enti"""
    if os.path.exists(HISTORY_FILE):
        with open(HISTORY_FILE, 'r') as f:
            return json.load(f)
    return {}

def save_history(history):
    """Salva lo storico aggiornato"""
    with open(HISTORY_FILE, 'w') as f:
        json.dump(history, f, indent=2)

def search_entity(numero_repertorio):
    """Esegue la ricerca di un ente sul RUNTS utilizzando il numero di repertorio"""
    logger.info(f"Ricerca dell'ente con numero repertorio: {numero_repertorio}")
    
    session = requests.Session()
    
    # Carica la pagina di ricerca per ottenere i token di sessione
    url = "https://servizi.lavoro.gov.it/runts/ricerca-online"
    try:
        response = session.get(url)
        response.raise_for_status()
    except requests.exceptions.RequestException as e:
        logger.error(f"Errore nel caricamento della pagina di ricerca: {e}")
        return None
    
    # Estrai eventuali token dalla pagina (se necessari)
    soup = BeautifulSoup(response.text, 'html.parser')
    
    # Prepara i dati per la ricerca (adatta in base alla struttura del form)
    search_data = {
        "Denominazione": "",
        "Comune": "",
        "Sezione": "",
        "CodiceFiscale": numero_repertorio,  # Usiamo il codice fiscale come numero di repertorio
        "NumeroRepertorio": "",
        "ReteAssociativa": ""
    }
    
    # Estrai eventuali token CSRF o altri campi nascosti necessari
    csrf_token = soup.find('input', {'name': '__RequestVerificationToken'})
    if csrf_token:
        search_data['__RequestVerificationToken'] = csrf_token['value']
    
    # Esegui la ricerca
    search_url = "https://servizi.lavoro.gov.it/runts/ricerca-online/Search"
    try:
        response = session.post(search_url, data=search_data)
        response.raise_for_status()
    except requests.exceptions.RequestException as e:
        logger.error(f"Errore nell'esecuzione della ricerca: {e}")
        return None
    
    # Estrai i dati dell'ente dalla pagina di risultati
    soup = BeautifulSoup(response.text, 'html.parser')
    
    # Estrai i dati dalla tabella dei risultati
    result_data = {}
    
    # Cerca la tabella dei risultati
    table = soup.find('table')
    if table:
        rows = table.find_all('tr')
        if len(rows) > 1:  # Se c'è almeno una riga oltre l'intestazione
            # Estrai i dati dalla prima riga di risultati
            cells = rows[1].find_all('td')
            if len(cells) >= 3:
                result_data = {
                    "denominazione": cells[0].text.strip() if cells[0] else "N/A",
                    "comune": cells[1].text.strip() if cells[1] else "N/A",
                    "sezione": cells[2].text.strip() if cells[2] else "N/A",
                    "data_controllo": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                }
    
    if not result_data:
        logger.warning(f"Nessun risultato trovato per il codice fiscale: {numero_repertorio}")
        # Restituisci comunque un risultato vuoto per registrare il controllo
        result_data = {
            "denominazione": "NON TROVATO",
            "comune": "NON TROVATO",
            "sezione": "NON TROVATO",
            "data_controllo": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        }
    
    return result_data

def send_notification(changes):
    """Invia una notifica email con le modifiche rilevate"""
    config = load_config()
    recipient_email = config["notifiche"]["email"]
    
    if not recipient_email:
        logger.warning("Nessun indirizzo email configurato per le notifiche")
        return
    
    # Crea il messaggio email
    subject = f"RUNTS Monitor: Rilevate {len(changes)} modifiche"
    
    # Prepara il contenuto dell'email
    email_content = "<h2>Modifiche rilevate nel monitoraggio RUNTS</h2>"
    email_content += "<table border='1' cellpadding='5' cellspacing='0'>"
    email_content += "<tr><th>Nome Ente</th><th>Codice Fiscale</th><th>Campo</th><th>Valore Precedente</th><th>Nuovo Valore</th></tr>"
    
    for change in changes:
        email_content += f"<tr>"
        email_content += f"<td>{change['nome']}</td>"
        email_content += f"<td>{change['numero_repertorio']}</td>"
        email_content += f"<td>{change['campo']}</td>"
        email_content += f"<td>{change['valore_precedente']}</td>"
        email_content += f"<td>{change['valore_nuovo']}</td>"
        email_content += "</tr>"
    
    email_content += "</table>"
    
    # Usa GitHub Actions per creare un'issue e inviare l'email
    # Crea un file nel repository che GitHub Actions leggerà
    notification_file = f"{DATA_DIR}/notification_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    with open(notification_file, 'w') as f:
        json.dump({
            "email": recipient_email,
            "subject": subject,
            "content": email_content,
            "changes": changes
        }, f, indent=2)
    
    logger.info(f"Notifica salvata in {notification_file} per l'elaborazione da parte di GitHub Actions")

def check_for_changes():
    """Controlla se ci sono modifiche nei dati degli enti monitorati"""
    config = load_config()
    history = load_history()
    changes = []
    
    for ente in config["enti"]:
        numero_repertorio = ente["numero_repertorio"]
        nome = ente["nome"]
        
        # Ricerca i dati attuali
        current_data = search_entity(numero_repertorio)
        
        if not current_data:
            logger.warning(f"Impossibile ottenere dati per l'ente {nome} ({numero_repertorio})")
            continue
        
        # Verifica se abbiamo già dati storici per questo ente
        if numero_repertorio in history:
            old_data = history[numero_repertorio]
            
            # Confronta i dati
            for key in current_data:
                if key != "data_controllo" and key in old_data:
                    if current_data[key] != old_data[key]:
                        changes.append({
                            "nome": nome,
                            "numero_repertorio": numero_repertorio,
                            "campo": key,
                            "valore_precedente": old_data[key],
                            "valore_nuovo": current_data[key]
                        })
                        logger.info(f"Rilevata modifica per {nome}: {key} è cambiato da '{old_data[key]}' a '{current_data[key]}'")
        else:
            logger.info(f"Prima rilevazione per l'ente {nome} ({numero_repertorio})")
        
        # Aggiorna lo storico
        history[numero_repertorio] = current_data
        
        # Piccola pausa per non sovraccaricare il server
        time.sleep(2)
    
    # Salva lo storico aggiornato
    save_history(history)
    
    # Se ci sono modifiche, invia una notifica
    if changes:
        logger.info(f"Rilevate {len(changes)} modifiche")
        send_notification(changes)
    else:
        logger.info("Nessuna modifica rilevata")
    
    return changes

if __name__ == "__main__":
    logger.info("Avvio del monitoraggio RUNTS")
    check_for_changes()
    logger.info("Monitoraggio completato")
