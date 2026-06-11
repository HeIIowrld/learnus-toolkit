const statusElement = document.getElementById("status");
const openButton = document.getElementById("openLearnUs");

chrome.tabs.query({ active: true, currentWindow: true }, ([tab]) => {
  const url = tab?.url || "";
  if (url.startsWith("https://ys.learnus.org/course/view.php")) {
    statusElement.textContent = "강의 페이지 하단의 LearnUs Downloader 패널에서 다운로드를 실행하세요.";
  } else if (url.startsWith("https://ys.learnus.org/")) {
    statusElement.textContent = "강의 페이지로 들어가면 다운로드 패널이 자동으로 표시됩니다.";
  } else {
    statusElement.textContent = "LearnUs 강의 페이지에서 사용할 수 있습니다.";
  }
});

openButton.addEventListener("click", () => {
  chrome.tabs.create({ url: "https://ys.learnus.org/" });
});
