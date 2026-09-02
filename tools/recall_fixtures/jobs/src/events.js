import { Inngest } from 'inngest'
const inngest = new Inngest({ id: 'demo' })
inngest.createFunction({ event: 'app/user.created' }, async () => {})
export const welcome = () => inngest.send({ name: 'app/user.created' })
export const oops    = () => inngest.send({ name: 'app/user.craeted' })  // <-- planted typo
