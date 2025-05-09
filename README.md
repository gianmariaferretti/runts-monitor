# RUNTS Monitor

## Panoramica
RUNTS Monitor è uno strumento automatizzato per monitorare il Registro Unico Nazionale del Terzo Settore (RUNTS) e rilevare nuovi documenti pubblicati per una lista predefinita di enti. Il sistema è particolarmente attento alla pubblicazione di bilanci, con priorità speciale per i bilanci dell'anno 2024.

## Funzionalità principali
- Monitoraggio automatico giornaliero dei documenti pubblicati sul RUNTS
- Rilevamento specifico di nuovi bilanci, con priorità per quelli del 2024
- Notifiche via email con formato professionale e chiaro
- Creazione automatica di issue su GitHub quando vengono rilevati nuovi documenti
- Archiviazione dello storico dei documenti per confronti futuri

## Come funziona
Il sistema utilizza Playwright per navigare sul portale RUNTS e BeautifulSoup per estrarre i dati. Per ogni ente configurato:
1. Ricerca l'ente per codice fiscale
2. Naviga alla pagina di dettaglio dell'ente
3. Estrae i documenti pubblicati (con focus sui bilanci)
4. Confronta i documenti con lo storico per identificare i nuovi
5. Se trova nuovi documenti, genera notifiche appropriate

## Installazione e configurazione

### Prerequisiti
- Python 3.9+
- Git
- Accesso a GitHub Actions

### Installazione
```bash
git clone https://github.com/yourname/runts-monitor.git
cd runts-monitor
pip install -r requirements.txt
python -m playwright install chromium
```

### Configurazione
1. Modifica `config.json` per aggiungere/rimuovere enti da monitorare:
```json
{
  "enti": [
    { "numero_repertorio": "CODICEFISCALE", "nome": "NOME_ENTE" },
    ...
  ],
  "notifiche": {
    "email": "tuo.indirizzo@esempio.it"
  }
}
```

## Esecuzione
### Manuale
```bash
python runts_monitor.py
```

### Automatica
Il monitoraggio viene eseguito automaticamente ogni giorno alle 8:00 UTC tramite GitHub Actions.

## Struttura del progetto
- `runts_monitor.py`: Script principale di monitoraggio
- `config.json`: Configurazione degli enti da monitorare
- `data/documents_history.json`: Archivio storico dei documenti
- `.github/workflows/runts-monitor.yml`: Configurazione GitHub Actions
- `requirements.txt`: Dipendenze Python

## Notifiche
### Email
Le notifiche email contengono:
- Un riepilogo dei nuovi documenti rilevati
- Una sezione speciale per i bilanci 2024 (se presenti)
- Una lista organizzata di tutti i nuovi documenti per ente

### GitHub Issues
Per ogni esecuzione che rileva modifiche, viene creata un'issue GitHub contenente:
- Un titolo che evidenzia la presenza di bilanci 2024 (se trovati)
- Sezioni separate per bilanci 2024, altri bilanci e altri documenti

## Troubleshooting
- Se il monitoraggio fallisce, verificare che il portale RUNTS sia accessibile
- In caso di errori di timeout, potrebbe essere necessario aumentare i valori di timeout nel codice
- Se un ente non viene trovato, verificare che il codice fiscale sia corretto in `config.json`

## Licenza
Questo progetto è di proprietà privata e non può essere ridistribuito senza autorizzazione.

---

Per maggiori informazioni o supporto, contattare gli sviluppatori o creare un'issue su GitHub.
