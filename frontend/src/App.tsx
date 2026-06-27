import { BrowserRouter, Routes, Route } from 'react-router-dom'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import Layout from './components/Layout'
import Home from './pages/Home'
import AlphaTerminal from './pages/AlphaTerminal'
import MacroScenario from './pages/MacroScenario'
import MonteCarlo from './pages/MonteCarlo'
import Optimizer from './pages/Optimizer'
import TimingEngine from './pages/TimingEngine'
import LensReport from './pages/LensReport'

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      retry: 1,
      staleTime: 30_000,
      refetchOnWindowFocus: false,
    },
  },
})

export default function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <BrowserRouter>
        <Routes>
          <Route path="/" element={<Layout />}>
            <Route index element={<Home />} />
            <Route path="terminal" element={<AlphaTerminal />} />
            <Route path="macro" element={<MacroScenario />} />
            <Route path="monte-carlo" element={<MonteCarlo />} />
            <Route path="optimizer" element={<Optimizer />} />
            <Route path="timing" element={<TimingEngine />} />
            <Route path="lens" element={<LensReport />} />
          </Route>
        </Routes>
      </BrowserRouter>
    </QueryClientProvider>
  )
}
