# MeteoRisk Light 1.2.4.95

Release client-side della logica convettiva, grandine, nebbia e tracking. La release .94 e quelle precedenti rimangono inalterate.

## Modifiche

- Il potenziale ambientale NWP è indipendente dall'innesco temporalesco; Sviluppo mostra i badge di potenziale mesociclonico senza dipendere da Nuovi Indici.
- Il codice WMO temporalesco è trattato come segnale istantaneo e non viene vincolato alla pioggia cumulata dell'ora.
- Il downburst può essere attivato anche da rovesci convettivi; livello zero del detector non viene promosso artificialmente.
- La grandine usa il massimo giornaliero per dimensione e probabilità, separato dall'ora rappresentativa di CAPE; i rovesci sono distinti dalla pioggia totale.
- Nebbia WMO 45/48 resta un segnale anche senza temperatura e dew point; l'indice è dichiarato euristico e non probabilistico.
- L'ora rappresentativa dei sistemi organizzati corrisponde all'ora in cui si verifica il segnale.
- Le associazioni SOI-cella sono limitate a 10 km e ai candidati dell'ultimo frame; il pannello segnala quando non esiste un abbinamento.
- La confidenza non calibrata non è mostrata come probabilità affidabile.

## File release

- Applicazione: `mri-light-1.2.4.95.html`
- Service Worker: cache applicativa, tile e frame aggiornate alla .95.
- `VERSION`, `README.md` e changelog in-app aggiornati.

Nessun deployment effettuato.
