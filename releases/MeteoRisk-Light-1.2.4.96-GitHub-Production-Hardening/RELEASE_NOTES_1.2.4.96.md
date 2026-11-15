# MeteoRisk Light 1.2.4.96

Release autonoma sopra la .95. Le release precedenti restano inalterate.

## Correzioni

- Il badge nebbia usa solo i codici di previsione WMO 45/48; i punteggi ambientali non generano allerte.
- L'indice ambientale richiede temperatura, dew point, vento e umidità; se manca un dato mostra N/D. La legenda dichiara che non è probabilità né misura di visibilità.
- I testi della nebbia non inventano valori di visibilità sotto 1 km, 500 m o 200 m.
- In confronto modelli il badge nebbia dichiara quando il segnale appare in un solo modello.
- Il punteggio euristico grandine viene chiamato indice di potenziale, non probabilità.
- I codici WMO 95-99 restano eventi nella vista giornaliera anche senza un accumulo di pioggia nell'ora precedente.

## Versioni

Applicazione `mri-light-1.2.4.96.html`; Service Worker e cache `.96`; `VERSION`, README e changelog in-app aggiornati.
