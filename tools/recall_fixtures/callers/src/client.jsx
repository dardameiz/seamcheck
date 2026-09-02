import axios from 'axios'
import Link from 'next/link'

export const loadTeams = () => axios.get('/api/teams')
export const makeTeam  = () => axios.post('/api/teams/create', {})

export function Nav({ router }) {
  return <nav>
    <Link href="/settings/billing">Billing</Link>
    <button onClick={() => router.push('/settings/billing')}>Go</button>
  </nav>
}

const params = new URLSearchParams(location.search)
export const q = params.get('/not-a-route-just-a-key')
export const cached = new Map().get('/also-not-a-route')
