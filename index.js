const express = require('express');
const cors = require('cors');

const app = express();
const PORT = process.env.PORT || 8080;

// CORS permissif (à restreindre si besoin)
app.use(cors());

app.get('/health', (_req, res) => res.status(200).send('OK'));
app.get('/', (_req, res) => res.status(200).send('Rcvo backend up'));

app.listen(PORT, () => console.log(`Server listening on port ${PORT}`));
