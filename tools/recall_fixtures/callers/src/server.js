const express = require('express')
const app = express()
app.get('/api/teams', (req, res) => res.json([]))
app.post('/api/teams/create', (req, res) => res.json({}))
app.get('/settings/billing', (req, res) => res.send('billing'))
app.get('/api/orphan', (req, res) => res.json({}))
module.exports = app
