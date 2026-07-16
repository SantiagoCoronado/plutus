import { Component, type ReactNode } from 'react'

interface Props {
  children: ReactNode
  /** change (e.g. the route path) resets the boundary so navigation recovers */
  resetKey?: string
}

interface State {
  error: Error | null
}

/** Route-level crash containment: a render-time exception blanks one page with
 * a retry, never the whole app (there was previously no boundary at all). */
export default class ErrorBoundary extends Component<Props, State> {
  state: State = { error: null }

  static getDerivedStateFromError(error: Error): State {
    return { error }
  }

  componentDidUpdate(prev: Props) {
    if (prev.resetKey !== this.props.resetKey && this.state.error) {
      this.setState({ error: null })
    }
  }

  render() {
    if (this.state.error) {
      return (
        <div className="mx-auto mt-16 max-w-lg rounded border border-red-900/60 bg-red-950/20 p-4 text-sm">
          <p className="font-semibold text-red-300">This page crashed.</p>
          <p className="mt-1 break-all text-xs text-red-400/80">
            {this.state.error.message}
          </p>
          <button
            className="mt-3 rounded border border-zinc-700 px-3 py-1 text-xs text-zinc-300 hover:bg-zinc-800"
            onClick={() => this.setState({ error: null })}
          >
            Try again
          </button>
        </div>
      )
    }
    return this.props.children
  }
}
