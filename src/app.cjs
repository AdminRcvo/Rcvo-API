'use strict';

const express = require('express');
const app = express();

app.use(express.json());

// Santé
app.get('/health', (req, res) => res.status(200).send('OK'));

// Route de base neutre (pour éviter les 502)
app.get('/', (req, res) => {
  res.json({ status: 'API OK', ts: new Date().toISOString() });
});

// NOTE: on branchera tes vraies routes plus tard, quand l'env sera GREEN
// ex: const clientsRouter = require('../routes/clients.js');
// app.use('/clients', clientsRouter);

module.exports = app;
