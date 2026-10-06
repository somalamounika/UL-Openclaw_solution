import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { BrowserRouter } from 'react-router-dom'
import { TooltipProvider } from '@/components/ui/tooltip'
import App from './App'
import './index.css'

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      refetchOnWindowFocus: false,
      // A failed /graph is nearly always "backend down"; one retry, then say so.
      retry: 1,
      // The API lives on localhost. React Query's default 'online' mode pauses every
      // query whenever navigator.onLine is false — which has nothing to do with
      // whether the local FastAPI service is reachable, and leaves the UI stuck
      // reporting "checking" forever with no error to show.
      networkMode: 'always',
    },
    mutations: { networkMode: 'always' },
  },
})

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>
      <TooltipProvider delayDuration={350}>
        <BrowserRouter>
          <App />
        </BrowserRouter>
      </TooltipProvider>
    </QueryClientProvider>
  </StrictMode>,
)
