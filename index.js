const express = require('express');
const app = express();
const PORT = process.env.PORT || 8080;

app.get('/health', (_req, res) => res.status(200).send('OK'));
app.get('/', (_req, res) => res.status(200).send('Rcvo backend up'));

app.listen(PORT, () => console.log(`Server listening on port ${PORT}`));
