import json
import os
import time
import asyncio
from datetime import datetime
import logging
import sys
from bs4 import BeautifulSoup
from playwright.async_api import async_playwright

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

def extract_text(soup, label_text):
    """Estrae testo da un elemento trovato con il selettore CSS"""
    try:
        # Cerca sia per elementi strong che contengono il testo che per text nodes
        element = soup.find('strong', string=lambda x: x and label_text in x)
        if element and element.find_next_sibling():
            return element.find_next_sibling().get_text(strip=True)
            
        # Cerca anche con una stringa diretta
        element = soup.find(string=lambda x: x and label_text in x)
        if element and element.find_parent() and element.find_parent().find_next_sibling():
            return element.find_parent().find_next_sibling().get_text(strip=True)
            
        return None
    except Exception as e:
        logger.error(f"Errore nell'estrazione di {label_text}: {e}")
        return None

def extract_person_data(soup, person_num):
    """Estrae i dati di una persona specifica"""
    try:
        person_section = soup.find(string=lambda x: x and f"Persona {person_num}" in x)
        if not person_section:
            return {}

        # Trova il container della persona
        person_container = person_section.find_parent('div')
        if not person_container:
            return {}

        # Estrai i dati della persona
        person_data = {
            'tipo': extract_text(soup, 'Tipo'),
            'rappresentante_legale': extract_text(soup, 'Rappresentante legale'),
            'codice_fiscale': extract_text(soup, 'Codice fiscale'),
            'nome': extract_text(soup, 'Nome'),
            'cognome': extract_text(soup, 'Cognome'),
            'data_nascita': extract_text(soup, 'Data di nascita'),
            'provincia': extract_text(soup, 'Provincia'),
            'comune': extract_text(soup, 'Comune'),
            'carica': extract_text(soup, 'Carica'),
            'data_nomina': extract_text(soup, 'Data nomina')
        }
        
        # Rimuovi i valori None
        return {k: v for k, v in person_data.items() if v is not None}
    except Exception as e:
        logger.error(f"Errore nell'estrazione di Persona {person_num}: {e}")
        return {}

def extract_documents(soup):
    """Estrae l'elenco dei documenti"""
    documents = []
    try:
        docs_section = soup.find('h2', string='Atti e documenti')
        if docs_section:
            table = docs_section.find_next('table')
            if table:
                rows = table.find_all('tr')[1:]  # Salta l'intestazione
                for row in rows:
                    cells = row.find_all('td')
                    if len(cells) >= 3:
                        doc = {
                            'documento': cells[0].get_text(strip=True),
                            'codice_pratica': cells[1].get_text(strip=True),
                            'data': cells[2].get_text(strip=True),
                            'allegato': 'Presente' if len(cells) > 3 and cells[3].find('a') else 'Non presente'
                        }
                        documents.append(doc)
    except Exception as e:
        logger.error(f"Errore nell'estrazione dei documenti: {e}")
        
    return documents

async def search_entity(page, codice_fiscale):
    """Cerca un ente usando Playwright"""
    logger.info(f"Ricerca dell'ente con codice fiscale: {codice_fiscale}")
    
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
    
    try:
        # Naviga alla pagina di ricerca
        await page.goto("https://servizi.lavoro.gov.it/runts/it-it/Ricerca-enti", wait_until="domcontentloaded")
        
        # Gestione popup cookie se presente
        try:
            accept_cookie = await page.wait_for_selector("button:has-text('ACCETTA')", timeout=5000)
            if accept_cookie:
                await accept_cookie.click()
                logger.info("Popup cookie accettato")
        except Exception as e:
            logger.info(f"Popup cookie non trovato o già accettato: {e}")
        
        # Inserisci il numero repertorio e cerca
        # Il selettore corretto è #dnn_ctr446_View_txtNumeroRepertorio per Numero Repertorio
        # o #CodiceFiscale per Codice Fiscale
        try:
            await page.fill("#CodiceFiscale", codice_fiscale)
        except Exception:
            try:
                await page.fill("#dnn_ctr446_View_txtCodiceFiscale", codice_fiscale)
            except Exception:
                logger.warning("Impossibile trovare il campo del codice fiscale, provo con altre opzioni")
                try:
                    # Prova un selettore più generico
                    await page.fill("input[id*='CodiceFiscale']", codice_fiscale)
                except Exception as e:
                    logger.error(f"Errore nel compilare il campo del codice fiscale: {e}")
                    return entity_data
        
        # Clicca sul pulsante CERCA
        try:
            await page.click("button:has-text('CERCA')")
        except Exception:
            try:
                await page.click("#dnn_ctr446_View_btnRicercaEnti")
            except Exception as e:
                logger.error(f"Errore nel cliccare il pulsante CERCA: {e}")
                return entity_data
        
        # Attendi che i risultati appaiano
        try:
            await page.wait_for_selector("table", timeout=10000)
        except Exception as e:
            logger.warning(f"Nessuna tabella di risultati trovata: {e}")
            return entity_data
        
        # Verifica se ci sono risultati
        no_results = await page.locator("text='Nessun risultato trovato'").count()
        if no_results > 0:
            logger.warning(f"Nessun risultato trovato per il codice fiscale: {codice_fiscale}")
            return entity_data
        
        # Estrai i dati di base dalla tabella
        rows = await page.locator("table tr").all()
        if len(rows) > 1:  # Se c'è almeno una riga oltre l'intestazione
            row = rows[1]
            cells = await row.locator("td").all()
            if len(cells) >= 3:
                entity_data["dati_base"] = {
                    "denominazione": await cells[0].inner_text(),
                    "comune": await cells[1].inner_text(),
                    "sezione": await cells[2].inner_text()
                }
        
        # Clicca sul pulsante Dettaglio
        try:
            await page.click("a:has-text('DETTAGLIO')")
        except Exception:
            try:
                await page.click("input[value='Dettaglio']")
            except Exception as e:
                logger.error(f"Errore nel cliccare il pulsante DETTAGLIO: {e}")
                return entity_data
        
        # Aspetta che la pagina di dettaglio carichi
        try:
            await page.wait_for_selector("h1, h2", timeout=10000)
        except Exception as e:
            logger.error(f"Errore nell'attesa della pagina di dettaglio: {e}")
            return entity_data
        
        # Estrai tutti i dati dalla pagina
        content = await page.content()
        soup = BeautifulSoup(content, "html.parser")
        
        # Dati generali dell'ente
        try:
            # Trova il titolo dell'ente
            h1 = soup.find('h1')
            h2 = soup.find('h2')
            entity_data["dati_ente"]["denominazione"] = h1.get_text(strip=True) if h1 else (h2.get_text(strip=True) if h2 else None)
            
            # Altri dati dell'ente
            entity_data["dati_ente"].update({
                "repertorio": extract_text(soup, "Repertorio"),
                "codice_fiscale": extract_text(soup, "Codice fiscale"),
                "data_iscrizione": extract_text(soup, "Iscritto il"),
                "data_iscrizione_sezione": extract_text(soup, "Iscritto nella sezione in data"),
                "sezione": extract_text(soup, "Sezione"),
                "forma_giuridica": extract_text(soup, "Forma Giuridica"),
                "email_pec": extract_text(soup, "Email PEC"),
                "ultimo_aggiornamento": extract_text(soup, "Ultimo aggiornamento statutario"),
                "atto_costitutivo": extract_text(soup, "Atto costitutivo")
            })
            
            # Rimuovi i valori None
            entity_data["dati_ente"] = {k: v for k, v in entity_data["dati_ente"].items() if v is not None}
            
            # Sede legale
            entity_data["sede_legale"] = {
                "stato": extract_text(soup, "Stato"),
                "provincia": extract_text(soup, "Provincia"),
                "comune": extract_text(soup, "Comune"),
                "indirizzo": extract_text(soup, "Indirizzo"),
                "civico": extract_text(soup, "Civico"),
                "cap": extract_text(soup, "CAP")
            }
            
            # Rimuovi i valori None
            entity_data["sede_legale"] = {k: v for k, v in entity_data["sede_legale"].items() if v is not None}
            
            # Estrai dati delle persone (fino a 10 persone)
            for i in range(1, 11):
                person_data = extract_person_data(soup, i)
                if person_data:
                    entity_data["persone"].append(person_data)
            
            # Estrai documenti
            entity_data["documenti"] = extract_documents(soup)
            
        except Exception as e:
            logger.error(f"Errore nell'estrazione dei dati dalla pagina di dettaglio: {e}")
        
        return entity_data
        
    except Exception as e:
        logger.error(f"Errore generale durante la ricerca dell'ente {codice_fiscale}: {e}")
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
        elif isinstance(v, list):
            for i, item in enumerate(v):
                if isinstance(item, dict):
                    items.extend(flatten_dict(item, f"{new_key}[{i}]").items())
                else:
                    items.append((f"{new_key}[{i}]", item))
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

async def process_entity(page, ente, history, all_changes):
    """Processa un singolo ente"""
    codice_fiscale = ente["numero_repertorio"]
    nome = ente["nome"]
    
    # Ricerca i dati attuali
    current_data = await search_entity(page, codice_fiscale)
    
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

async def check_for_changes():
    """Controlla se ci sono modifiche nei dati degli enti monitorati"""
    config = load_config()
    history = load_history()
    all_changes = []
    
    async with async_playwright() as playwright:
        # Usa un browser Chromium
        browser = await playwright.chromium.launch(headless=True)
        context = await browser.new_context()
        page = await context.new_page()
        
        try:
            for ente in config["enti"]:
                await process_entity(page, ente, history, all_changes)
                
                # Piccola pausa per non sovraccaricare il server
                await asyncio.sleep(2)
        
        finally:
            await browser.close()
        
    # Salva lo storico aggiornato
    save_history(history)
    
    # Se ci sono modifiche, invia una notifica
    if all_changes:
        logger.info(f"Rilevate {len(all_changes)} modifiche in totale")
        send_notification(all_changes)
    else:
        logger.info("Nessuna modifica rilevata")
    
    return all_changes

# Funzione principale
def main():
    logger.info("Avvio del monitoraggio RUNTS")
    asyncio.run(check_for_changes())
    logger.info("Monitoraggio completato")

if __name__ == "__main__":
    main()
