const title = document.querySelector('#title')
const detail = document.querySelector('#detail')
const metadata = document.querySelector('#metadata')
const restartButton = document.querySelector('#restart')
const logsButton = document.querySelector('#logs')
const { invoke } = window.__TAURI__.core

let lastStatus = null

function renderStatus(nextTitle, nextDetail, status = null) {
  title.textContent = nextTitle
  detail.textContent = nextDetail
  lastStatus = status

  if (status) {
    metadata.hidden = false
    metadata.textContent = [
      `Backend: ${status.backendUrl}`,
      `Repo: ${status.repoRoot}`,
      `Studio dist: ${status.studioDistDir}`,
      `Logs: ${status.logsDir}`,
    ].join('\n')
  }
}

async function start() {
  renderStatus('Starting local services', 'Launching the backend and preparing Studio.')
  try {
    const status = await invoke('start_orcheo')
    renderStatus('Opening Studio', 'The backend is healthy. Loading Orcheo Studio.', status)
    window.location.replace(status.backendUrl)
  } catch (error) {
    renderStatus(
      'Orcheo could not start',
      typeof error === 'string' ? error : String(error),
      lastStatus,
    )
  }
}

restartButton.addEventListener('click', async () => {
  restartButton.disabled = true
  renderStatus('Restarting local services', 'Stopping and relaunching Orcheo.')
  try {
    const status = await invoke('restart_orcheo')
    renderStatus('Opening Studio', 'The backend is healthy. Loading Orcheo Studio.', status)
    window.location.replace(status.backendUrl)
  } catch (error) {
    renderStatus('Orcheo could not restart', typeof error === 'string' ? error : String(error))
  } finally {
    restartButton.disabled = false
  }
})

logsButton.addEventListener('click', async () => {
  try {
    await invoke('open_logs')
  } catch (error) {
    renderStatus('Could not open logs', typeof error === 'string' ? error : String(error), lastStatus)
  }
})

start()
