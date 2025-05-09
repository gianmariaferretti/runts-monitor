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
HISTORY_FILE = f"{DATA_DIR}/documents_history.json"

# Assicuriamoci che la directory data esista
if not os.path.exists(DATA_DIR):
    os.makedirs(DATA_DIR)

def load_config():
    """Carica la configurazione dal file config.json"""
    with open(CONFIG_FILE, 'r') as f:
        return json.load(f)

def load_history():
    """Carica lo storico dei documenti degli enti"""
    if os.path.exists(HISTORY_FILE):
        with open(HISTORY_FILE, 'r') as f:
            return json.load(f)
    return {}

def save_history(history):
    """Salva lo storico aggiornato"""
    with open(HISTORY_FILE, 'w') as f:
        json.dump(history, f, indent=2)

def extract_documents(soup):
    """Estrae l'elenco dei documenti con particolare attenzione ai bilanci"""
    documents = []
    try:
        # Cerca la sezione "Atti e documenti"
        docs_section = None
        
        # Metodi multipli per trovare la sezione documenti
        selectors = [
            # Metodo 1: Cerca titoli che contengono esattamente "Atti e documenti"
            lambda s: s.find(['h1', 'h2', 'h3', 'h4'], string="Atti e documenti"),
            
            # Metodo 2: Cerca titoli che contengono in qualche modo "Atti e documenti"
            lambda s: s.find(['h1', 'h2', 'h3', 'h4'], string=lambda x: x and "Atti e documenti" in x),
            
            # Metodo 3: Cerca div con testo "Atti e documenti"
            lambda s: s.find('div', string=lambda x: x and "Atti e documenti" in x),
            
            # Metodo 4: Cerca qualsiasi elemento con testo "Atti e documenti"
            lambda s: s.find(string=lambda x: x and "Atti e documenti" in x),
            
            # Metodo 5: Cerca tabelle con intestazioni tipiche di documenti
            lambda s: s.find('table', lambda t: t.find('th', string=lambda x: x and ("Documento" in x or "documento" in x or "Allegato" in x)))
        ]
        
        # Prova ciascun selettore finché non troviamo una sezione
        for selector in selectors:
            docs_section = selector(soup)
            if docs_section:
                logger.info(f"Trovata sezione documenti")
                break
        
        # Se abbiamo trovato una sezione testo (non una tabella), dobbiamo trovare la tabella associata
        tables = []
        
        if docs_section and docs_section.name != 'table':
            # Se abbiamo trovato un elemento di testo, cerchiamo la tabella successiva
            if hasattr(docs_section, 'find_next'):
                table = docs_section.find_next('table')
                if table:
                    tables.append(table)
            # Se abbiamo trovato una stringa di testo, cerchiamo la tabella dal suo genitore
            elif docs_section.parent and hasattr(docs_section.parent, 'find_next'):
                table = docs_section.parent.find_next('table')
                if table:
                    tables.append(table)
        elif docs_section and docs_section.name == 'table':
            # Se abbiamo trovato direttamente una tabella
            tables.append(docs_section)
        
        # Se non abbiamo trovato tabelle, cerca tutte le tabelle che potrebbero contenere documenti
        if not tables:
            logger.info("Cercando tabelle generiche che potrebbero contenere documenti")
            tables = soup.find_all('table')
        
        # Processa ciascuna tabella trovata
        for table in tables:
            # Assicuriamoci che la tabella abbia delle righe
            rows = table.find_all('tr')
            if len(rows) <= 1:  # Solo intestazione o vuota
                continue
                
            # Ottieni le intestazioni
            header_cells = rows[0].find_all(['th', 'td'])
            headers = [cell.get_text(strip=True).lower() for cell in header_cells]
            
            # Verifica che questa sembri essere una tabella di documenti
            if not any(keyword in ' '.join(headers) for keyword in 
                      ['documento', 'file', 'pratica', 'codice', 'allegato', 'data']):
                continue
            
            # Mappa le colonne
            col_index = {
                'documento': next((i for i, h in enumerate(headers) if any(kw in h for kw in ['documento', 'file', 'titolo'])), 0),
                'codice': next((i for i, h in enumerate(headers) if any(kw in h for kw in ['codice', 'pratica', 'id'])), 1),
                'data': next((i for i, h in enumerate(headers) if any(kw in h for kw in ['data', 'anno', 'period'])), 2),
                'allegato': next((i for i, h in enumerate(headers) if any(kw in h for kw in ['allegato', 'download', 'file'])), 3)
            }
            
            # Processa le righe (esclusa l'intestazione)
            for row in rows[1:]:
                cells = row.find_all(['td', 'th'])
                if len(cells) < 2:  # Non abbastanza celle
                    continue
                
                try:
                    # Estrai i dati con gestione degli errori per ogni cella
                    doc_type = ""
                    if col_index['documento'] < len(cells):
                        doc_type = cells[col_index['documento']].get_text(strip=True)
                    
                    code = ""
                    if col_index['codice'] < len(cells):
                        code = cells[col_index['codice']].get_text(strip=True)
                    
                    date = ""
                    if col_index['data'] < len(cells) and cells[col_index['data']]:
                        date_text = cells[col_index['data']].get_text(strip=True)
                        # Cerca un anno in formato 20XX o date formattate
                        year_match = re.search(r'20\d{2}', date_text)
                        if year_match:
                            date = year_match.group(0)
                        else:
                            date = date_text
                    
                    has_attachment = "No"
                    if col_index['allegato'] < len(cells):
                        attachment_cell = cells[col_index['allegato']]
                        if (attachment_cell.find('a') or attachment_cell.find('img') or 
                            'download' in attachment_cell.get('class', []) or
                            attachment_cell.find(lambda tag: tag.name == 'i' and 'download' in tag.get('class', []))):
                            has_attachment = "Sì"
                    
                    # Se abbiamo almeno un tipo di documento o un codice pratica valido, aggiungilo
                    if doc_type or code:
                        document_data = {
                            "tipo_documento": doc_type,
                            "codice_pratica": code,
                            "anno": date,
                            "ha_allegato": has_attachment
                        }
                        # Evita documenti duplicati verificando se già esiste un doc con stesso tipo e codice
                        if not any(d['tipo_documento'] == doc_type and d['codice_pratica'] == code 
                                   for d in documents):
                            documents.append(document_data)
                except Exception as e:
                    logger.error(f"Errore nell'estrazione dei dati del documento: {e}")
        
        # Ordina i documenti: prima i bilanci, poi per anno (decrescente)
        documents.sort(key=lambda x: (
            0 if "BILANCIO" in x["tipo_documento"].upper() else 1,  # Bilanci prima
            -1 * int(x["anno"]) if x["anno"].isdigit() else 0       # Anni più recenti prima
        ))
        
        logger.info(f"Estratti {len(documents)} documenti")
                        
    except Exception as e:
        logger.error(f"Errore generale nell'estrazione dei documenti: {e}")
    
    return documents

async def extract_entity_documents(page, codice_fiscale, nome_ente):
    """Estrae solo i documenti di un ente (versione ottimizzata)"""
    logger.info(f"Ricerca documenti per l'ente {nome_ente} ({codice_fiscale})")
    
    # Creiamo il contenitore per i dati risultato (solo documenti e info minimali)
    entity_data = {
        "data_controllo": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "codice_fiscale": codice_fiscale,
        "nome": nome_ente,
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
                    break
        except Exception:
            pass
        
        # Inserisci il codice fiscale nel campo appropriato
        filled = False
        for selector in ["#CodiceFiscale", "#dnn_ctr446_View_txtCodiceFiscale", "input[id*='CodiceFiscale']"]:
            try:
                input_field = await page.query_selector(selector)
                if input_field:
                    await input_field.fill(codice_fiscale)
                    filled = True
                    break
            except Exception:
                continue
        
        if not filled:
            # Prova un approccio più generico con JavaScript
            try:
                await page.evaluate("""() => {
                    const inputs = Array.from(document.querySelectorAll('input'));
                    const cfInput = inputs.find(i => i.id && i.id.toLowerCase().includes('codice') && i.id.toLowerCase().includes('fiscale'));
                    if (cfInput) cfInput.value = arguments[0];
                }""", codice_fiscale)
                filled = True
            except Exception as e:
                logger.error(f"Errore nella compilazione del campo: {e}")
                return entity_data
        
        # Clicca sul pulsante CERCA
        clicked = False
        for selector in ["button:has-text('CERCA')", "#dnn_ctr446_View_btnRicercaEnti", "input[value='CERCA']", "button.btn-primary"]:
            try:
                search_button = await page.query_selector(selector)
                if search_button:
                    await search_button.click()
                    clicked = True
                    await page.wait_for_load_state("networkidle", timeout=15000)
                    break
            except Exception:
                continue
        
        if not clicked:
            try:
                await page.evaluate("""() => {
                    const buttons = Array.from(document.querySelectorAll('button, input[type="submit"]'));
                    const searchBtn = buttons.find(b => b.textContent && b.textContent.includes('CERCA'));
                    if (searchBtn) searchBtn.click();
                }""")
                clicked = True
                await page.wait_for_load_state("networkidle", timeout=15000)
            except Exception as e:
                logger.error(f"Errore nel cliccare il pulsante: {e}")
                return entity_data
        
        # Attendi che i risultati della ricerca siano caricati
        await asyncio.sleep(3)
        
        # Verifica se ci sono risultati
        page_content = await page.content()
        if "Nessun risultato trovato" in page_content:
            logger.warning(f"Nessun risultato trovato per {codice_fiscale}")
            return entity_data
        
        # Clicca sul pulsante Dettaglio
        dettaglio_clicked = False
        for selector in ["a:has-text('DETTAGLIO')", "input[value='Dettaglio']", ".btn:has-text('Dettaglio')"]:
            try:
                dettaglio_button = await page.query_selector(selector)
                if dettaglio_button:
                    await dettaglio_button.click()
                    dettaglio_clicked = True
                    break
            except Exception:
                continue
        
        if not dettaglio_clicked:
            try:
                await page.evaluate("""() => {
                    const links = Array.from(document.querySelectorAll('a, input, button'));
                    const detailLink = links.find(l => l.textContent && l.textContent.includes('DETTAGLIO') || l.value && l.value.includes('Dettaglio'));
                    if (detailLink) detailLink.click();
                }""")
                dettaglio_clicked = True
            except Exception as e:
                logger.error(f"Errore nel cliccare DETTAGLIO: {e}")
                return entity_data
        
        # Aspetta che la pagina di dettaglio carichi
        await page.wait_for_load_state("networkidle", timeout=20000)
        await asyncio.sleep(5)
        
        # Estrai solo i documenti dalla pagina (ottimizzazione)
        content = await page.content()
        soup = BeautifulSoup(content, "html.parser")
        
        # Estrai documenti (questa è l'unica parte che ci interessa)
        entity_data["documenti"] = extract_documents(soup)
        logger.info(f"Estratti {len(entity_data['documenti'])} documenti per {nome_ente}")
        
        return entity_data
        
    except Exception as e:
        logger.error(f"Errore durante l'estrazione documenti per {nome_ente}: {e}")
        return entity_data

def compare_documents(old_data, new_data, nome_ente):
    """Confronta i documenti con particolare attenzione ai bilanci 2024"""
    changes = []
    bilancio_2024_changes = []  # Lista separata per i bilanci 2024
    
    # Ottieni documenti precedenti e attuali
    old_docs = old_data.get("documenti", [])
    new_docs = new_data.get("documenti", [])
    
    # Crea dizionari per il confronto rapido
    old_docs_dict = {f"{doc['tipo_documento']}_{doc['codice_pratica']}_{doc['anno']}": doc for doc in old_docs}
    
    # Cerca documenti nuovi
    for doc in new_docs:
        doc_key = f"{doc['tipo_documento']}_{doc['codice_pratica']}_{doc['anno']}"
        
        # Se questo documento non esisteva prima, è nuovo
        if doc_key not in old_docs_dict:
            is_bilancio = "BILANCIO" in doc["tipo_documento"].upper()
            year = doc["anno"]
            
            # Crea l'oggetto di modifica
            change = {
                "nome": nome_ente,
                "codice_fiscale": new_data["codice_fiscale"],
                "valore_precedente": "N/A",
            }
            
            # Personalizziamo in base al tipo di documento e all'anno
            if is_bilancio and year == "2024":
                change["campo"] = "Nuovo bilancio 2024 pubblicato"
                change["valore_nuovo"] = f"BILANCIO 2024 (codice: {doc['codice_pratica']})"
                change["priorita"] = "alta"
                bilancio_2024_changes.append(change)
            elif is_bilancio:
                change["campo"] = f"Nuovo bilancio {year} pubblicato"
                change["valore_nuovo"] = f"Bilancio {year} (codice: {doc['codice_pratica']})"
                change["priorita"] = "media"
                changes.append(change)
            else:
                change["campo"] = "Nuovo documento pubblicato"
                change["valore_nuovo"] = f"{doc['tipo_documento']} {year} (codice: {doc['codice_pratica']})"
                change["priorita"] = "bassa"
                changes.append(change)
    
    # Mettiamo i bilanci 2024 all'inizio della lista per dargli massima priorità
    return bilancio_2024_changes + changes

def send_notification(changes):
    """Invia una notifica email con evidenza speciale per i bilanci 2024"""
    config = load_config()
    recipient_email = config["notifiche"]["email"]
    
    if not recipient_email:
        logger.warning("Nessun indirizzo email configurato per le notifiche")
        return
    
    # Raggruppa le modifiche per ente
    changes_by_entity = {}
    bilanci_2024 = {}
    altri_bilanci = {}
    
    for change in changes:
        entity_key = f"{change['nome']} ({change['codice_fiscale']})"
        if entity_key not in changes_by_entity:
            changes_by_entity[entity_key] = []
        
        changes_by_entity[entity_key].append(change)
        
        # Identifica specificamente i bilanci 2024 e gli altri bilanci
        if change['campo'] == "Nuovo bilancio 2024 pubblicato":
            if entity_key not in bilanci_2024:
                bilanci_2024[entity_key] = []
            bilanci_2024[entity_key].append(change['valore_nuovo'])
        elif "Nuovo bilancio" in change['campo'] and "pubblicato" in change['campo']:
            if entity_key not in altri_bilanci:
                altri_bilanci[entity_key] = []
            altri_bilanci[entity_key].append(change['valore_nuovo'])
    
    # Conta il numero di entità con modifiche e bilanci 2024
    num_entities = len(changes_by_entity)
    num_bilanci_2024 = sum(len(bilanci) for bilanci in bilanci_2024.values())
    
    # Crea il messaggio email
    today = datetime.now().strftime("%d/%m/%Y")
    
    # Personalizza l'oggetto in base al contenuto
    if num_bilanci_2024 > 0:
        if num_bilanci_2024 == 1:
            subject = f"URGENTE: Pubblicato 1 bilancio 2024 sul RUNTS ({today})"
        else:
            subject = f"URGENTE: Pubblicati {num_bilanci_2024} bilanci 2024 sul RUNTS ({today})"
    else:
        subject = f"Aggiornamento RUNTS: Nuovi documenti disponibili ({today})"
    
    # Prepara il contenuto dell'email
    email_content = f"""
    <html>
    <head>
        <style>
            body {{ font-family: 'Segoe UI', Arial, sans-serif; line-height: 1.6; color: #333; margin: 0; padding: 0; background-color: #f9f9f9; }}
            .container {{ max-width: 650px; margin: 20px auto; background-color: #fff; border-radius: 6px; box-shadow: 0 3px 10px rgba(0,0,0,0.1); overflow: hidden; }}
            .header {{ background-color: #3a6ea5; padding: 25px 30px; color: white; }}
            .header h1 {{ margin: 0 0 10px 0; font-size: 24px; font-weight: 500; }}
            .content {{ padding: 30px; }}
            .intro {{ margin-bottom: 30px; }}
            .summary {{ background-color: #f5f8fa; padding: 20px; margin: 0 0 30px 0; border-radius: 6px; border-left: 4px solid #3a6ea5; }}
            h2 {{ font-size: 20px; margin: 0 0 15px 0; color: #2c3e50; font-weight: 500; }}
            .entity {{ margin: 35px 0; }}
            .entity-header {{ margin-bottom: 15px; }}
            .entity-header h3 {{ font-size: 18px; margin: 0 0 5px 0; color: #2c3e50; }}
            .entity-header p {{ margin: 0; color: #7f8c8d; font-size: 14px; }}
            .highlight {{ background-color: #e8f4f8; padding: 15px; margin: 15px 0; border-radius: 6px; border-left: 4px solid #2980b9; }}
            .highlight-urgent {{ background-color: #fff8e8; padding: 15px; margin: 15px 0; border-radius: 6px; border-left: 4px solid #e67e22; }}
            .highlight h4, .highlight-urgent h4 {{ margin: 0 0 10px 0; font-size: 16px; }}
            .highlight h4 {{ color: #2980b9; }}
            .highlight-urgent h4 {{ color: #e67e22; }}
            .highlight ul, .highlight-urgent ul {{ margin: 10px 0; padding-left: 25px; }}
            table {{ border-collapse: collapse; width: 100%; margin: 15px 0; border-radius: 6px; overflow: hidden; box-shadow: 0 1px 3px rgba(0,0,0,0.1); }}
            th {{ background-color: #f2f2f2; text-align: left; padding: 12px 15px; font-size: 15px; color: #444; font-weight: 500; }}
            td {{ padding: 10px 15px; border-top: 1px solid #eee; font-size: 14px; }}
            tr:nth-child(even) {{ background-color: #f9f9f9; }}
            tr:hover {{ background-color: #f5f5f5; }}
            .bilancio-2024 {{ background-color: #fff8e8; font-weight: bold; }}
            .footer {{ padding: 20px 30px; background-color: #f5f8fa; font-size: 14px; color: #7f8c8d; text-align: center; border-top: 1px solid #eee; }}
            .button {{ display: inline-block; background-color: #3a6ea5; color: white; padding: 10px 20px; text-decoration: none; border-radius: 4px; margin-top: 10px; }}
        </style>
    </head>
    <body>
        <div class="container">
            <div class="header">
                <h1>Monitoraggio RUNTS: {"BILANCI 2024 PUBBLICATI" if num_bilanci_2024 > 0 else "Nuovi documenti disponibili"}</h1>
                <div style="color: #e5e5e5; font-size: 14px;">{today}</div>
            </div>
            <div class="content">
                <div class="intro">
                    <p>Gentile utente,</p>
    """
    
    # Personalizza l'introduzione in base alla presenza di bilanci 2024
    if num_bilanci_2024 > 0:
        email_content += f"""
                    <p>ti avvisiamo che <strong>sono stati pubblicati {num_bilanci_2024} bilanci 2024</strong> sul Registro Unico Nazionale del Terzo Settore per gli enti che stai monitorando.</p>
                    <p>Ti suggeriamo di prendere visione di questi bilanci il prima possibile.</p>
        """
    else:
        email_content += """
                    <p>durante l'ultimo controllo del Registro Unico Nazionale del Terzo Settore, abbiamo rilevato nuovi documenti pubblicati per alcuni degli enti che stai monitorando.</p>
        """
    
    email_content += """
                </div>
                
                <div class="summary">
                    <h2>Riepilogo delle novità</h2>
    """
    
    # Prima di tutto, evidenzia i bilanci 2024 se presenti
    if bilanci_2024:
        email_content += """
            <div class="highlight-urgent">
                <h4>🚨 BILANCI 2024 PUBBLICATI</h4>
                <ul>
        """
        for entity, docs in bilanci_2024.items():
            for doc in docs:
                email_content += f"<li><strong>{entity}</strong>: {doc}</li>"
        email_content += """
                </ul>
                <p style="margin-top: 15px; font-size: 13px; color: #666;"><strong>IMPORTANTE:</strong> Ti invitiamo a consultare al più presto questi bilanci recenti.</p>
            </div>
        """
    
    # Poi, mostra gli altri bilanci se presenti
    if altri_bilanci:
        email_content += """
            <div class="highlight">
                <h4>📊 Altri bilanci pubblicati</h4>
                <ul>
        """
        for entity, docs in altri_bilanci.items():
            for doc in docs:
                email_content += f"<li><strong>{entity}</strong>: {doc}</li>"
        email_content += """
                </ul>
            </div>
        """
    
    email_content += "</div>"  # Fine div summary
    
    # Dettagli per ogni ente
    for entity_name, entity_changes in changes_by_entity.items():
        email_content += f"""
        <div class="entity">
            <div class="entity-header">
                <h3>{entity_name}</h3>
                <p>{len(entity_changes)} nuovi documenti disponibili</p>
            </div>
        """
        
        # Tabella con tutti i nuovi documenti
        email_content += """
            <table>
                <tr>
                    <th style="width: 40%;">Documento</th>
                    <th style="width: 60%;">Dettagli</th>
                </tr>
        """
        
        # Ordina i documenti per priorità
        for change in sorted(entity_changes, key=lambda x: 0 if "bilancio 2024" in x['campo'].lower() else (1 if "bilancio" in x['campo'].lower() else 2)):
            css_class = ""
            prefix = "📄 "
            
            if "bilancio 2024" in change['campo'].lower():
                css_class = "class='bilancio-2024'"
                prefix = "🚨 "
            elif "bilancio" in change['campo'].lower():
                prefix = "📊 "
            
            email_content += f"""
                <tr {css_class}>
                    <td>{prefix}{change['campo'].replace("Nuovo ", "").replace(" pubblicato", "")}</td>
                    <td>{change['valore_nuovo']}</td>
                </tr>
            """
        
        email_content += """
            </table>
        </div>
        """
    
    # Chiusura email
    email_content += """
                <div style="margin-top: 40px;">
                    <p>I documenti sono disponibili per la consultazione sul portale RUNTS. Puoi accedervi direttamente cercando l'ente di interesse sul <a href="https://servizi.lavoro.gov.it/runts/it-it/Ricerca-enti">sito ufficiale del Registro</a>.</p>
                    <p>Ti ricordiamo che questo monitoraggio è configurato per controllare regolarmente le variazioni documentali degli enti selezionati.</p>
                    <p>Cordiali saluti,<br>
                    Il sistema di monitoraggio RUNTS</p>
                </div>
            </div>
            <div class="footer">
                <p>Questa comunicazione è stata generata automaticamente dal sistema di monitoraggio RUNTS. Si prega di non rispondere a questa email.</p>
                <p>Se desideri modificare la configurazione del monitoraggio o aggiungere nuovi enti da controllare, puoi farlo aggiornando il file di configurazione nel repository.</p>
            </div>
        </div>
    </body>
    </html>
    """
    
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

async def process_entity(page, ente, history, all_changes):
    """Processa un singolo ente, concentrandosi solo sui documenti"""
    codice_fiscale = ente["numero_repertorio"]
    nome = ente["nome"]
    
    # Estrai solo i documenti (ottimizzato)
    current_data = await extract_entity_documents(page, codice_fiscale, nome)
    
    # Verifica se abbiamo già dati storici per questo ente
    if codice_fiscale in history:
        old_data = history[codice_fiscale]
        
        # Confronta SOLO i documenti
        changes = compare_documents(old_data, current_data, nome)
        if changes:
            all_changes.extend(changes)
            logger.info(f"Rilevati {len(changes)} nuovi documenti per l'ente {nome}")
    else:
        logger.info(f"Prima rilevazione per l'ente {nome}")
    
    # Aggiorna lo storico
    history[codice_fiscale] = current_data
    
    # Breve pausa tra un ente e l'altro
    await asyncio.sleep(2)

async def check_for_new_documents():
    """Controlla se ci sono nuovi documenti per gli enti monitorati"""
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
    
    # Se ci sono nuovi documenti, invia una notifica
    if all_changes:
        logger.info(f"Rilevati {len(all_changes)} nuovi documenti in totale")
        send_notification(all_changes)
    else:
        logger.info("Nessun nuovo documento rilevato")
    
    return all_changes

# Funzione principale
def main():
    logger.info("Avvio del monitoraggio documenti RUNTS")
    asyncio.run(check_for_new_documents())
    logger.info("Monitoraggio completato")

if __name__ == "__main__":
    main()
