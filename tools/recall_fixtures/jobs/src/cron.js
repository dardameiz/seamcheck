import cron from 'node-cron'
import agenda from './agenda.js'
agenda.define('reindex', async () => {})
agenda.every('0 3 * * *', 'reindex')
agenda.every('0 3 * * * * *', 'reindex')   // <-- 7 fields, invalid
