/**
 * Rcvo Backend - index.js
 * Sert à la fois l'UI statique et l'API sous /api/*
 */

const express = require('express');
const path = require('path');
const bodyParser = require('body-parser');
// Si vous utilisez dotenv pour vos variables d'env, décommentez :
require('dotenv').config();

const app = express();
const PORT = process.env.PORT || 8000;

// --- Middlewares ---
// JSON parsing
app.use(bodyParser.json());

// (Optionnel) CORS si vous faites des tests hors du même domaine
// const cors = require('cors');
// app.use(cors());

// --- Service de l'UI statique ---
// Tous les fichiers du front (HTML/CSS/JS/images) dans public/
app.use(express.static(path.join(__dirname, 'public')));

// --- API ROUTES ---

// Exemple de route de santé
app.get('/api/sync/health', (req, res) => {
  res.json({ status: 'OK' });
});

// Exemple de route d'authentification Admin
app.post('/api/admin/login', async (req, res) => {
  try {
    const { email, password } = req.body;
    // --- INSÉREZ ICI VOTRE LOGIQUE DE CONNEXION ---
    // Ex : vérifiez dans DB, générez JWT, etc.
    // Pour test, renvoyons un jeton factice :
    return res.json({ token: 'jeton-de-test' });
  } catch (err) {
    console.error('Login error:', err);
    return res.status(500).json({ error: 'InternalServerError' });
  }
});

// Exemple de route d'enregistrement Admin
app.post('/api/admin/register', async (req, res) => {
  try {
    const { email, password } = req.body;
    // --- INSÉREZ ICI VOTRE LOGIQUE D\'INSCRIPTION ---
    // Ex : créez utilisateur en DB, envoyez mail, etc.
    return res.status(201).json({ message: 'Utilisateur créé' });
  } catch (err) {
    console.error('Register error:', err);
    return res.status(500).json({ error: 'InternalServerError' });
  }
});

// --- Catch-all pour le front-end ---
// Toute autre route GET renvoie index.html pour le routage client-side
app.get('/*', (req, res) => {
  res.sendFile(path.join(__dirname, 'public', 'index.html'));
});

// --- Démarrage du serveur ---
app.listen(PORT, () => {
  console.log(`Server listening on port ${PORT}`);
});
