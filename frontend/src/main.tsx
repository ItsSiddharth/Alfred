import React from 'react'
import ReactDOM from 'react-dom/client'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import App from './App'
import './theme/index.css'

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      // Don't retry on 4xx errors — they're user-fixable, not transient.
      retry: (failureCount, error) => {
        if (error instanceof Error && error.message.includes('API 4')) return false
        return failureCount < 2
      },
      staleTime: 30_000,
    },
  },
})

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <QueryClientProvider client={queryClient}>
      <App />
    </QueryClientProvider>
  </React.StrictMode>,
)