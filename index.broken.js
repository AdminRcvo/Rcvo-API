const PORT = process.env.PORT || 8000;
app.listen(PORT, '0.0.0.0', () => {
  console.log(\`RCVO API démarrée sur le port \${PORT}\`);
});
