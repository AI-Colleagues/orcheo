const title = document.querySelector('#title')
const detail = document.querySelector('#detail')
const actions = document.querySelector('#actions')
const restartButton = document.querySelector('#restart')
const logsButton = document.querySelector('#logs')
const { invoke } = window.__TAURI__.core

function renderStatus(nextTitle, nextDetail, { showActions = false } = {}) {
  title.textContent = nextTitle
  detail.textContent = nextDetail
  actions.hidden = !showActions
}

async function start() {
  renderStatus('Starting Orcheo', 'Launching local services...')
  try {
    const status = await invoke('start_orcheo')
    window.location.replace(status.backendUrl)
  } catch (error) {
    renderStatus('Orcheo could not start', typeof error === 'string' ? error : String(error), {
      showActions: true,
    })
  }
}

restartButton.addEventListener('click', async () => {
  restartButton.disabled = true
  renderStatus('Restarting Orcheo', 'Stopping and relaunching local services...')
  try {
    const status = await invoke('restart_orcheo')
    window.location.replace(status.backendUrl)
  } catch (error) {
    renderStatus('Orcheo could not restart', typeof error === 'string' ? error : String(error), {
      showActions: true,
    })
  } finally {
    restartButton.disabled = false
  }
})

logsButton.addEventListener('click', async () => {
  try {
    await invoke('open_logs')
  } catch (error) {
    renderStatus('Could not open logs', typeof error === 'string' ? error : String(error), {
      showActions: true,
    })
  }
})

start()
