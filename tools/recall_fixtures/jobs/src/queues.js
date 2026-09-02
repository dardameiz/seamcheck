import { Queue, Worker } from 'bullmq'
const INVOICES = 'send-invoice'
export const invoices = new Queue(INVOICES)
new Worker(INVOICES, async job => { /* ... */ })
new Worker('nightly-report', async job => { /* ... */ })

export async function bill(id) {
  await invoices.add('send-invoice', { id })
  await invoices.add('send-invoyce', { id })   // <-- planted typo
}
