import requests
import json
import os
import time
from bs4 import BeautifulSoup
from datetime import datetime
import logging
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

def extract_text(soup, selector, default="N/A"):
    """Estrae testo da un elemento trovato con il selettore CSS"""
    element = soup.select_one(selector)
    if element:
        return element.get_text(strip=True)
    return default

def search_entity(codice_fiscale):
    """Esegue la ricerca di un ente sul RUNTS utilizzando il codice fiscale"""
    logger.info(f"Ricerca dell'ente con codice fiscale: {codice_fiscale}")
    
    session = requests.Session()
    
    # URL corretto per la ricerca
    url = "https://servizi.lavoro.gov.it/runts/it-it/Ricerca-enti"
    try:
        response = session.get(url)
        response.raise_for_status()
    except requests.exceptions.RequestException as e:
        logger.error(f"Errore nel caricamento della pagina di ricerca: {e}")
        return None
    
    # Creiamo il contenitore per i dati risultato
    entity_data = {
        "data_controllo": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "codice_fiscale": codice_fiscale,
        "dati_base": {},
        "dati_ente": {},
        "sede_legale": {},
        "persone": [],
        "documenti": []
    }
    
    # Estrai eventuali token dalla pagina
    soup = BeautifulSoup(response.text, 'html.parser')
    
    # Form di ricerca
    form_data = {
        "Denominazione": "",
        "Comune": "",
        "Sezione": "",
        "CodiceFiscale": codice_fiscale,
        "NumeroRepertorio": "",
        "ReteAssociativa": ""
    }
    
    # Estrai eventuali token CSRF o altri campi nascosti necessari
    hidden_inputs = soup.find_all('input', type='hidden')
    for input_tag in hidden_inputs:
        if input_tag.get('name'):
            form_data[input_tag.get('name')] = input_tag.get('value', '')
    
    # Esegui la ricerca
    search_url = url
    try:
        response = session.post(search_url, data=form_data)
        response.raise_for_status()
    except requests.exceptions.RequestException as e:
        logger.error(f"Errore nell'esecuzione della ricerca: {e}")
        return None
    
    # Analizza i risultati della ricerca
    soup = BeautifulSoup(response.text, 'html.parser')
    
    # Verifica se ci sono risultati
    table = soup.find('table')
    if not table or len(table.find_all('tr')) <= 1:
        logger.warning(f"Nessun risultato trovato per il codice fiscale: {codice_fiscale}")
        return entity_data
    
    # Estrai i dati di base dalla tabella dei risultati
    rows = table.find_all('tr')
    if len(rows) > 1:  # Se c'è almeno una riga oltre l'intestazione
        cells = rows[1].find_all('td')
        if len(cells) >= 3:
            entity_data["dati_base"] = {
                "denominazione": cells[0].text.strip() if cells[0] else "N/A",
                "comune": cells[1].text.strip() if cells[1] else "N/A",
                "sezione": cells[2].text.strip() if cells[2] else "N/A"
            }
    
    # Trova il pulsante "DETTAGLIO" e ottieni l'URL di dettaglio
    dettaglio_button = soup.find('a', text='DETTAGLIO')
    if not dettaglio_button:
        logger.warning(f"Pulsante DETTAGLIO non trovato per l'ente con codice fiscale: {codice_fiscale}")
        return entity_data
    
    # Vai alla pagina di dettaglio
    detail_url = dettaglio_button.get('href')
    if not detail_url.startswith('http'):
        # Se l'URL è relativo, lo rendiamo assoluto
        base_url = "https://servizi.lavoro.gov.it"
        detail_url = f"{base_url}{detail_url}"
    
    try:
        response = session.get(detail_url)
        response.raise_for_status()
    except requests.exceptions.RequestException as e:
        logger.error(f"Errore nel caricamento della pagina di dettaglio: {e}")
        return entity_data
    
    # Analizza la pagina di dettaglio
    soup = BeautifulSoup(response.text, 'html.parser')
    
    # Estrai tutti i dati rilevanti dalla pagina di dettaglio
    
    # Dati generali dell'ente
    try:
        entity_data["dati_ente"] = {
            "denominazione": extract_text(soup, 'h2'),
            "repertorio": extract_text(soup, 'strong:contains("Repertorio:") + span'),
            "codice_fiscale": extract_text(soup, 'strong:contains("Codice fiscale:") + span'),
            "data_iscrizione": extract_text(soup, 'strong:contains("Iscritto il") + span'),
            "data_iscrizione_sezione": extract_text(soup, 'strong:contains("Iscritto nella sezione in data") + span'),
            "sezione": extract_text(soup, 'strong:contains("Sezione") + span'),
            "forma_giuridica": extract_text(soup, 'strong:contains("Forma Giuridica") + span'),
            "email_pec": extract_text(soup, 'strong:contains("Email PEC") + span'),
            "ultimo_aggiornamento": extract_text(soup, 'strong:contains("Ultimo aggiornamento statutario") + span'),
            "atto_costitutivo": extract_text(soup, 'strong:contains("Atto costitutivo") + span')
        }
    except Exception as e:
        logger.error(f"Errore nell'estrazione dei dati ente: {e}")
    
    # Estrai i dati sulle persone
    try:
        person_sections = soup.find_all('div', string=re.compile(r'Persona \d+'))
        for section in person_sections:
            person_data = {}
            # Trova il container della persona
            person_container = section.find_parent('div')
            if person_container:
                # Estrai tutti i dati della persona
                person_data = {
                    "tipo": extract_text(person_container, 'strong:contains("Tipo") + span'),
                    "rappresentante_legale": extract_text(person_container, 'strong:contains("Rappresentante legale") + span'),
                    "codice_fiscale": extract_text(person_container, 'strong:contains("Codice fiscale") + span'),
                    "nome": extract_text(person_container, 'strong:contains("Nome") + span'),
                    "cognome": extract_text(person_container, 'strong:contains("Cognome") + span'),
                    "stato": extract_text(person_container, 'strong:contains("Stato") + span'),
                    "data_nascita": extract_text(person_container, 'strong:contains("Data di nascita") + span'),
                    "provincia": extract_text(person_container, 'strong:contains("Provincia") + span'),
                    "comune": extract_text(person_container, 'strong:contains("Comune") + span'),
                    "carica": extract_text(person_container, 'strong:contains("Carica") + span'),
                    "data_nomina": extract_text(person_container, 'strong:contains("Data nomina") + span')
                }
                entity_data["persone"].append(person_data)
    except Exception as e:
        logger.error(f"Errore nell'estrazione dei dati delle persone: {e}")
    
    # Estrai i dati sui documenti
    try:
        documents_section = soup.find('h2', text='Atti e documenti')
        if documents_section:
            documents_table = documents_section.find_next('table')
            if documents_table:
                rows = documents_table.find_all('tr')[1:]  # Salta l'intestazione
                for row in rows:
                    cells = row.find_all('td')
                    if len(cells) >= 4:
                        document_data = {
                            "documento": extract_text(cells[0]) if cells[0] else "N/A",
                            "codice_pratica": extract_text(cells[1]) if cells[1] else "N/A",
                            "data": extract_text(cells[2]) if cells[2] else "N/A",
                            "allegato": "Presente" if cells[3].find('a') else "Non presente"
                        }
                        entity_data["documenti"].append(document_data)
    except Exception as e:
        logger.error(f"Errore nell'estrazione dei dati dei documenti: {e}")
    
    return entity_data

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
        email_content += f"<td>{change['codice_fiscale']}</td>"
        email_content += f"<td>{change['campo']}</td>"
        email_content += f"<td>{change['valore_precedente']}</td>"
        email_content += f"<td>{change['valore_nuovo']}</td>"
        email_content += "</tr>"
    
    email_content += "</table>"
    
    # Usa GitHub Actions per creare un'issue e inviare l'email
    notification_file = f"{DATA_DIR}/notification_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    with open(notification_file, 'w') as f:
        json.dump({
            "email": recipient_email,
            "subject": subject,
            "content": email_content,
            "changes": changes
        }, f, indent=2)
    
    logger.info(f"Notifica salvata in {notification_file} per l'elaborazione da parte di GitHub Actions")

def flatten_dict(d, prefix=''):
    """Appiattisce un dizionario nidificato in una singola dimensione"""
    items = []
    for k, v in d.items():
        new_key = f"{prefix}.{k}" if prefix else k
        if isinstance(v, dict):
            items.extend(flatten_dict(v, new_key).items())
        else:
            items.append((new_key, v))
    return dict(items)

def compare_entities(old_data, new_data, codice_fiscale, nome_ente):
    """Confronta i dati vecchi e nuovi di un ente e restituisce le modifiche"""
    changes = []
    
    # Appiattisci i dizionari per confrontare facilmente anche i sottolivelli
    flat_old = flatten_dict(old_data)
    flat_new = flatten_dict(new_data)
    
    # Confronta i dati appiattiti
    for key in set(flat_old.keys()) | set(flat_new.keys()):
        # Salta la data di controllo
        if key == 'data_controllo':
            continue
            
        old_value = flat_old.get(key, "N/A")
        new_value = flat_new.get(key, "N/A")
        
        if old_value != new_value:
            changes.append({
                "nome": nome_ente,
                "codice_fiscale": codice_fiscale,
                "campo": key,
                "valore_precedente": old_value,
                "valore_nuovo": new_value
            })
    
    return changes

def check_for_changes():
    """Controlla se ci sono modifiche nei dati degli enti monitorati"""
    config = load_config()
    history = load_history()
    all_changes = []
    
    for ente in config["enti"]:
        codice_fiscale = ente["numero_repertorio"]
        nome = ente["nome"]
        
        # Ricerca i dati attuali
        current_data = search_entity(codice_fiscale)
        
        if not current_data:
            logger.warning(f"Impossibile ottenere dati per l'ente {nome} ({codice_fiscale})")
            continue
        
        # Verifica se abbiamo già dati storici per questo ente
        if codice_fiscale in history:
            old_data = history[codice_fiscale]
            
            # Confronta i dati vecchi e nuovi
            changes = compare_entities(old_data, current_data, codice_fiscale, nome)
            if changes:
                all_changes.extend(changes)
                logger.info(f"Rilevate {len(changes)} modifiche per l'ente {nome} ({codice_fiscale})")
        else:
            logger.info(f"Prima rilevazione per l'ente {nome} ({codice_fiscale})")
        
        # Aggiorna lo storico
        history[codice_fiscale] = current_data
        
        # Piccola pausa per non sovraccaricare il server
        time.sleep(2)
    
    # Salva lo storico aggiornato
    save_history(history)
    
    # Se ci sono modifiche, invia una notifica
    if all_changes:
        logger.info(f"Rilevate {len(all_changes)} modifiche in totale")
        send_notification(all_changes)
    else:
        logger.info("Nessuna modifica rilevata")
    
    return all_changes

if __name__ == "__main__":
    logger.info("Avvio del monitoraggio RUNTS")
    check_for_changes()
    logger.info("Monitoraggio completato")
