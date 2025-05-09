import json
import os
import time
import asyncio
from datetime import datetime
import logging
import sys
import re
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
        # Cerca elementi strong che contengono il testo
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
        # Cerca sezioni che contengono "Persona X"
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
        await page.goto("https://servizi.lavoro.gov.it/runts/it-it/Ricerca-enti", wait_until="networkidle", timeout=30000)
        
        # Attendi che la pagina sia completamente caricata
        await asyncio.sleep(2)
        
        # Gestione popup cookie se presente
        try:
            # Prova diversi selettori per il pulsante dei cookie
            for cookie_selector in ["button:has-text('ACCETTA')", "a.cookieClose", "button.cookie-accept"]:
                cookie_button = await page.query_selector(cookie_selector)
                if cookie_button:
                    await cookie_button.click()
                    logger.info(f"Popup cookie accettato con selettore: {cookie_selector}")
                    break
        except Exception as e:
            logger.info(f"Popup cookie non trovato o già accettato: {e}")
        
        # Inserisci il codice fiscale nel campo appropriato
        # Prova diversi selettori per il campo
        filled = False
        for selector in ["#CodiceFiscale", "#dnn_ctr446_View_txtCodiceFiscale", "input[id*='CodiceFiscale']"]:
            try:
                input_field = await page.query_selector(selector)
                if input_field:
                    await input_field.fill(codice_fiscale)
                    filled = True
                    logger.info(f"Campo codice fiscale compilato con selettore: {selector}")
                    break
            except Exception:
                continue
        
        if not filled:
            logger.warning("Impossibile trovare il campo del codice fiscale")
            # Prova un approccio più generico con JavaScript
            try:
                await page.evaluate("""() => {
                    const inputs = Array.from(document.querySelectorAll('input'));
                    const cfInput = inputs.find(i => i.id && i.id.toLowerCase().includes('codice') && i.id.toLowerCase().includes('fiscale'));
                    if (cfInput) cfInput.value = arguments[0];
                }""", codice_fiscale)
                filled = True
                logger.info("Campo codice fiscale compilato tramite JavaScript")
            except Exception as e:
                logger.error(f"Errore anche con JavaScript: {e}")
                return entity_data
        
        # Clicca sul pulsante CERCA
        clicked = False
        for selector in ["button:has-text('CERCA')", "#dnn_ctr446_View_btnRicercaEnti", "input[value='CERCA']", "button.btn-primary"]:
            try:
                search_button = await page.query_selector(selector)
                if search_button:
                    await search_button.click()
                    clicked = True
                    logger.info(f"Pulsante CERCA cliccato con selettore: {selector}")
                    await page.wait_for_load_state("networkidle", timeout=15000)
                    break
            except Exception:
                continue
        
        if not clicked:
            logger.warning("Impossibile trovare il pulsante CERCA, provo con JavaScript")
            try:
                await page.evaluate("""() => {
                    const buttons = Array.from(document.querySelectorAll('button, input[type="submit"]'));
                    const searchBtn = buttons.find(b => b.textContent && b.textContent.includes('CERCA'));
                    if (searchBtn) searchBtn.click();
                }""")
                clicked = True
                logger.info("Pulsante CERCA cliccato tramite JavaScript")
                await page.wait_for_load_state("networkidle", timeout=15000)
            except Exception as e:
                logger.error(f"Errore nel cliccare con JavaScript: {e}")
                return entity_data
        
        # Attendi che i risultati della ricerca siano caricati
        await asyncio.sleep(3)
        
        # Verifica se ci sono risultati
        page_content = await page.content()
        if "Nessun risultato trovato" in page_content:
            logger.warning(f"Nessun risultato trovato per il codice fiscale: {codice_fiscale}")
            return entity_data
        
        # Controlla se c'è una tabella di risultati
        table = await page.query_selector("table")
        if not table:
            logger.warning(f"Nessuna tabella di risultati trovata per il codice fiscale: {codice_fiscale}")
            return entity_data
        
        # Estrai i dati di base dalla tabella
        rows = await table.query_selector_all("tr")
        if len(rows) > 1:
            row = rows[1]
            cells = await row.query_selector_all("td")
            if len(cells) >= 3:
                entity_data["dati_base"] = {
                    "denominazione": await cells[0].inner_text(),
                    "comune": await cells[1].inner_text(),
                    "sezione": await cells[2].inner_text()
                }
                logger.info(f"Dati base estratti: {entity_data['dati_base']}")
        
        # Clicca sul pulsante Dettaglio
        dettaglio_clicked = False
        for selector in ["a:has-text('DETTAGLIO')", "input[value='Dettaglio']", ".btn:has-text('Dettaglio')"]:
            try:
                dettaglio_button = await page.query_selector(selector)
                if dettaglio_button:
                    await dettaglio_button.click()
                    dettaglio_clicked = True
                    logger.info(f"Pulsante DETTAGLIO cliccato con selettore: {selector}")
                    break
            except Exception:
                continue
        
        if not dettaglio_clicked:
            logger.warning("Impossibile trovare il pulsante DETTAGLIO, provo con JavaScript")
            try:
                await page.evaluate("""() => {
                    const links = Array.from(document.querySelectorAll('a, input, button'));
                    const detailLink = links.find(l => l.textContent && l.textContent.includes('DETTAGLIO') || l.value && l.value.includes('Dettaglio'));
                    if (detailLink) detailLink.click();
                }""")
                dettaglio_clicked = True
                logger.info("Pulsante DETTAGLIO cliccato tramite JavaScript")
            except Exception as e:
                logger.error(f"Errore nel cliccare DETTAGLIO con JavaScript: {e}")
                return entity_data
        
        # Aspetta che la pagina di dettaglio carichi completamente
        # NON aspettiamo elementi h1/h2 visibili (che potrebbero essere nascosti)
        # ma aspettiamo che la navigazione sia completa
        await page.wait_for_load_state("networkidle", timeout=20000)
        await asyncio.sleep(5)  # Pausa per assicurarci che la pagina sia completamente renderizzata
        
        # Verifica che siamo nella pagina di dettaglio controllando l'URL
        current_url = page.url
        if "Ente" in current_url or "Dettaglio" in current_url:
            logger.info(f"Siamo nella pagina di dettaglio: {current_url}")
        else:
            logger.warning(f"URL inaspettato, potremmo non essere nella pagina di dettaglio: {current_url}")
        
        # Estrai tutti i dati dalla pagina
        content = await page.content()
        soup = BeautifulSoup(content, "html.parser")
        
        # Dati ente
        title_text = None
        title_elements = soup.select(".ente_titolo-xl, .titolo-ente, h2:not([style*='display: none']), h3")
        if title_elements:
            title_text = title_elements[0].get_text(strip=True)
            entity_data["dati_ente"]["denominazione"] = title_text
            logger.info(f"Trovato titolo ente: {title_text}")
        
        # Estrai altri dati dell'ente
        for field, labels in {
            "repertorio": ["Repertorio"],
            "codice_fiscale": ["Codice fiscale"],
            "data_iscrizione": ["Iscritto il"],
            "sezione": ["Sezione"],
            "forma_giuridica": ["Forma Giuridica"],
            "email_pec": ["Email PEC"],
            "atto_costitutivo": ["Atto costitutivo"]
        }.items():
            for label in labels:
                value = extract_text(soup, label)
                if value:
                    entity_data["dati_ente"][field] = value
                    break
        
        # Sede legale
        for field, label in {
            "stato": "Stato",
            "provincia": "Provincia",
            "comune": "Comune",
            "indirizzo": "Indirizzo",
            "civico": "Civico",
            "cap": "CAP"
        }.items():
            value = extract_text(soup, label)
            if value:
                entity_data["sede_legale"][field] = value
        
        # Estrai dati delle persone (fino a 10 persone)
        for i in range(1, 11):
            person_data = extract_person_data(soup, i)
            if person_data:
                entity_data["persone"].append(person_data)
                logger.info(f"Estratti dati della persona {i}")
        
        # Estrai documenti
        entity_data["documenti"] = extract_documents(soup)
        logger.info(f"Estratti {len(entity_data['documenti'])} documenti")
        
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
    
    # Breve pausa tra un ente e l'altro
    await asyncio.sleep(2)

async def check_for_changes():
    """Controlla se ci sono modifiche nei dati degli enti monitorati"""
    config = load_config()
    history = load_history()
    all_changes = []
    
    async with async_playwright() as playwright:
        # Usa un browser Chromium con impostazioni più permissive
        browser = await playwright.chromium.launch(
            headless=True,
            args=['--disable-web-security', '--no-sandbox', '--disable-features=site-per-process']
        )
        context = await browser.new_context(
            viewport={'width': 1920, 'height': 1080},
            user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/90.0.4430.212 Safari/537.36'
        )
        page = await context.new_page()
        
        try:
            for ente in config["enti"]:
                await process_entity(page, ente, history, all_changes)
        
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
