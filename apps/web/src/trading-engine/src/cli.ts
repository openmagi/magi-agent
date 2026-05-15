import type { DaemonStatus } from './daemon.js'
import type { HeartbeatResult } from './heartbeat.js'

export interface ParsedCommand {
  command: string
  options: Record<string, string>
}

export interface CommandDaemon {
  start(): Promise<void>
  stop(): Promise<void>
  getStatus(): DaemonStatus
  isRunning(): boolean
}

export interface CommandHeartbeat {
  run(): Promise<HeartbeatResult>
}

export interface RunCommandDeps {
  daemon: CommandDaemon
  heartbeat: CommandHeartbeat
  log: (msg: string) => void
}

export function parseCommand(args: string[]): ParsedCommand {
  const command = args[2] ?? ''
  const options: Record<string, string> = {}

  for (let i = 3; i < args.length; i++) {
    const arg = args[i]
    if (arg !== undefined && arg.startsWith('--') && i + 1 < args.length) {
      const key = arg.slice(2)
      const value = args[i + 1]
      if (value !== undefined) {
        options[key] = value
        i++ // skip value
      }
    }
  }

  return { command, options }
}

export async function runCommand(
  command: string,
  _options: Record<string, string>,
  deps: RunCommandDeps,
): Promise<void> {
  const { daemon, heartbeat, log } = deps

  switch (command) {
    case 'start': {
      try {
        await daemon.start()
        log('Engine started successfully')
      } catch (error: unknown) {
        const msg = error instanceof Error ? error.message : String(error)
        log(`Error: ${msg}`)
      }
      break
    }

    case 'stop': {
      try {
        await daemon.stop()
        log('Engine stopped successfully')
      } catch (error: unknown) {
        const msg = error instanceof Error ? error.message : String(error)
        log(`Error: ${msg}`)
      }
      break
    }

    case 'status': {
      const status = daemon.getStatus()
      if (status.running) {
        log(`Status: Running`)
        log(`PID: ${status.pid}`)
        log(`Uptime: ${formatUptime(status.uptimeMs)}`)
        log(`Ticks: ${status.tickCount}`)
      } else {
        log('Status: Stopped (not running)')
        if (status.tickCount > 0) {
          log(`Last tick count: ${status.tickCount}`)
        }
      }
      break
    }

    case 'heartbeat': {
      const result = await heartbeat.run()
      log(result.scratchpadUpdate)
      if (result.alert !== null) {
        log(`\nALERT: ${result.alert}`)
      }
      if (result.restarted) {
        log('\nDaemon was not running - restart needed')
      }
      break
    }

    case 'close-all': {
      log('Close-all: delegating to orchestrator...')
      // This will be connected to ApexOrchestrator in the future
      log('Close-all command acknowledged')
      break
    }

    case 'reflect': {
      log('Reflect: triggering analysis...')
      // This will be connected to ReflectAnalyzer in the future
      log('Reflect command acknowledged')
      break
    }

    default: {
      log('Usage: engine <start|stop|status|heartbeat|close-all|reflect>')
      break
    }
  }
}

function formatUptime(ms: number): string {
  const seconds = Math.floor(ms / 1000)
  const minutes = Math.floor(seconds / 60)
  const hours = Math.floor(minutes / 60)

  if (hours > 0) {
    return `${hours}h ${minutes % 60}m`
  }
  if (minutes > 0) {
    return `${minutes}m ${seconds % 60}s`
  }
  return `${seconds}s`
}
