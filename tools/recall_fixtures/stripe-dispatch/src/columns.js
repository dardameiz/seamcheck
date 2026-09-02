// Strings that LOOK like Stripe event names and are database columns. The shape-only
// matcher scored roughly one true positive in eighteen on exactly this.
export const COLUMNS = ['users.id', 'invoice.total', 'product.id', 'charge.amount', 'customer.name']
