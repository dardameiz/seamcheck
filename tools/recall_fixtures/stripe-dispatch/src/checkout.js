import Stripe from 'stripe'
const stripe = new Stripe(process.env.STRIPE_SECRET_KEY)

export async function startCheckout(priceId) {
  return stripe.checkout.sessions.create({ line_items: [{ price: priceId }] })
}
export async function refund(chargeId) {
  return stripe.refunds.create({ charge: chargeId })
}
