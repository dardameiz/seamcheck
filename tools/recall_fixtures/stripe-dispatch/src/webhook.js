import Stripe from 'stripe'
const stripe = new Stripe(process.env.STRIPE_SECRET_KEY)

export async function handler(body, signature) {
  const event = stripe.webhooks.constructEvent(body, signature, process.env.WH_SECRET)
  switch (event.type) {
    case 'checkout.session.completed':
      return recordOrder(event)
    case 'customer.subscription.updated':
      return syncPlan(event)
  }
}
